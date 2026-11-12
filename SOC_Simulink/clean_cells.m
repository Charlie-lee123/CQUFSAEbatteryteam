function cr = clean_cells(p, cells_mv, bad_mask)
%CLEAN_CELLS  Clean one round of cell voltages (mV) into a usable full string.
%
%   cr = clean_cells(p, cells_mv, bad_mask)
%
%   Ported 1:1 from soc_est.clean_cells. Steps:
%     range filter -> MAD/IQR outlier threshold ->
%     split "isolated break" vs "continuous module offset" ->
%     isolated breaks are back-filled with the healthy mean (user's spec);
%     continuous module-offset runs keep their true values (just flagged).
%   cr is a struct with:
%     cells(125x1)  back-filled full string (mV)
%     mean_mv, median_mv, gap_mv, pack_mv
%     residual (logical)   mean/median gap too large -> don't trust voltage
%     ok (logical)         enough healthy cells to use
%     module_offset (logical)  a whole module is offset (real imbalance)
%     min_mv, max_mv       from TRUE readings only (never back-filled values)
%     n_raw, n_unusable, bad_idx(1-based)

    n = p.n_cells;
    values = double(cells_mv(:));
    if length(values) < n
        values = [values; NaN(n - length(values), 1)];
    elseif length(values) > n
        values = values(1:n);
    end

    if isempty(bad_mask)
        mask = false(n, 1);
    else
        mask = logical(bad_mask(:));
        if length(mask) < n, mask = [mask; false(n - length(mask), 1)]; end
        if length(mask) > n, mask = mask(1:n); end
    end

    % ---- collect healthy (in-range, present, not device-bad) readings ----
    healthy = [];
    n_raw = 0;
    for i = 1:n
        v = values(i);
        if isnan(v) || mask(i), continue; end
        if ~(v >= p.cell_min_mv && v <= p.cell_max_mv), continue; end
        n_raw = n_raw + 1;
        healthy = [healthy; v];   %#ok<AGROW>
    end

    if isempty(healthy)
        cr.cells = zeros(n, 1);
        cr.mean_mv = 0; cr.median_mv = 0; cr.gap_mv = 0;
        cr.residual = false; cr.ok = false; cr.module_offset = false;
        cr.min_mv = 0; cr.max_mv = 0;
        cr.n_raw = 0; cr.n_unusable = n; cr.pack_mv = 0;
        cr.bad_idx = (1:n)';
        return;
    end

    med = median(healthy);
    mad = median(abs(healthy - med));
    q = quantile_sort(healthy);            % [Q1, Q2, Q3]
    iqr = max(0.0, q(3) - q(1));
    limit = max([p.mad_k * 1.4826 * mad, p.mad_floor_mv, p.iqr_k * iqr]);

    % ---- per-position flag: NaN=unavailable, 0=in-range, +/-1=outlier ----
    flagdir = NaN(n, 1);
    for i = 1:n
        v = values(i);
        if isnan(v) || mask(i) || ~(v >= p.cell_min_mv && v <= p.cell_max_mv)
            flagdir(i) = NaN;
        elseif abs(v - med) > limit
            flagdir(i) = sign(v - med);
        else
            flagdir(i) = 0;
        end
    end

    % ---- split continuous runs: isolated break vs module offset ----
    % run_min = half a module (12 for E74). A 12+ continuous, same-sign run is
    % treated as real module-level imbalance (kept); shorter runs are isolated
    % breaks (filled). Over-range / missing are NaN -> always filled.
    per_module = p.cells_per_module;
    run_min = max(2, floor(per_module * p.module_run_ratio));
    keep = false(n, 1);
    module_offset = false;
    i = 1;
    while i <= n
        sgn = flagdir(i);
        if isnan(sgn) || sgn == 0
            keep(i) = (sgn == 0);
            i = i + 1;
            continue;
        end
        j = i;
        while j <= n && flagdir(j) == sgn
            j = j + 1;
        end
        if (j - i) >= run_min
            module_offset = true;
            for k = i:j-1
                keep(k) = true;
            end
        end
        i = j;
    end

    accepted = values(keep);
    if isempty(accepted)
        accepted = healthy;               % every cell flagged -> fall back
    end
    meanv = mean(accepted);
    med2 = median(accepted);
    gap = abs(meanv - med2);
    ok = (length(accepted) >= floor(n * p.min_healthy_ratio));

    % ---- back-fill everything not kept with the healthy mean ----
    fill = values;
    bad_idx = [];
    for k = 1:n
        if keep(k)
            fill(k) = values(k);
        else
            fill(k) = meanv;
            bad_idx = [bad_idx; k];        %#ok<AGROW>
        end
    end

    [lo_mv, hi_mv, ~] = robust_extremes(accepted, p.extreme_gap_mv, p.extreme_drop_max);

    cr.cells = fill;
    cr.mean_mv = meanv;
    cr.median_mv = med2;
    cr.gap_mv = gap;
    cr.residual = (gap > p.mean_med_max_mv);
    cr.ok = ok;
    cr.module_offset = module_offset;
    cr.min_mv = lo_mv;
    cr.max_mv = hi_mv;
    cr.n_raw = n_raw;
    cr.n_unusable = n - n_raw;
    cr.pack_mv = meanv * n;
    cr.bad_idx = bad_idx;
end

% ---------------------------------------------------------------- locals

function q = quantile_sort(v)
% [Q1, Q2, Q3] (25/50/75%) by linear interpolation (matches statistics.quantiles).
    x = sort(v(:));
    N = length(x);
    q = zeros(1, 3);
    for m = 1:3
        pos = m * (N + 1) / 4;            % 1-based position (exclusive method)
        lo = floor(pos); hi = ceil(pos);
        if lo < 1, lo = 1; end
        if hi > N, hi = N; end
        if lo == hi
            q(m) = x(lo);
        else
            frac = pos - lo;
            q(m) = x(lo) + frac * (x(hi) - x(lo));
        end
    end
end

function [lo_mv, hi_mv, dropped] = robust_extremes(accepted, gap_mv, max_drop)
% Drop only "isolated to absurd" endpoints; a whole-module offset is NOT
% mistaken for a bad point (its neighbour is offset too, so the gap is small).
    x = sort(accepted(:));
    L = length(x);
    if L == 0
        lo_mv = 0; hi_mv = 0; dropped = 0; return;
    end
    if L == 1
        lo_mv = x(1); hi_mv = x(1); dropped = 0; return;
    end
    lo = 1; hi = L; dropped = 0;
    for d = 1:max_drop
        if (hi - lo) < 2, break; end
        if (x(lo + 1) - x(lo)) > gap_mv
            lo = lo + 1; dropped = dropped + 1;
        else
            break;
        end
    end
    for d = 1:max_drop
        if (hi - lo) < 2, break; end
        if (x(hi) - x(hi - 1)) > gap_mv
            hi = hi - 1; dropped = dropped + 1;
        else
            break;
        end
    end
    lo_mv = x(lo); hi_mv = x(hi);
end
