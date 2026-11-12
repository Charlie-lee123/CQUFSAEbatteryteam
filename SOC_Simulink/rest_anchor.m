function [soc, anchored] = rest_anchor(p, ts, mean_mv, i_eff, i_valid, temp_c, soc)
%REST_ANCHOR  The only strong SOC correction: when the pack is quiescent,
%voltage is stable and temperature is in range, pull SOC toward the OCV value.
%
%   [soc, anchored] = rest_anchor(p, ts, mean_mv, i_eff, i_valid, temp_c, soc)
%
%   Gain 0.3, single-step cap +/-15%. Uses a fixed-size circular buffer for the
%   rest window (codegen-friendly). Mirrors soc_est.SocEstimator._rest_anchor.
    persistent buf head cnt first rest_since
    NBUF = 512;
    if isempty(buf)
        buf = zeros(NBUF, 2);
        head = 1; cnt = 0; first = 1; rest_since = NaN;
    end

    anchored = false;
    if isnan(ts) || ~i_valid || abs(i_eff) > p.rest_i_max_a
        rest_since = NaN; return;
    end
    if ~(temp_c >= p.rest_t_min_c && temp_c <= p.rest_t_max_c)
        rest_since = NaN; return;
    end

    % prune points older than the rest window, then append this one
    cut = ts - p.rest_window_s;
    while cnt > 0 && buf(first, 1) < cut
        first = mod(first, NBUF) + 1;
        cnt = cnt - 1;
    end
    buf(head, :) = [ts, mean_mv];
    head = mod(head, NBUF) + 1;
    if cnt < NBUF, cnt = cnt + 1; end

    if cnt < 2
        rest_since = ts; return;
    end

    % peak-to-peak of mean voltage over the window
    mn = Inf; mx = -Inf;
    for k = 1:cnt
        idx = mod(first - 1 + k - 1, NBUF) + 1;
        v = buf(idx, 2);
        if v < mn, mn = v; end
        if v > mx, mx = v; end
    end
    if (mx - mn) > p.rest_stable_mv
        rest_since = ts; return;          % still moving -> restart timer
    end

    if isnan(rest_since)
        rest_since = ts;
    end
    if (ts - rest_since) < p.rest_window_s
        return;                          % not quiet long enough yet
    end

    % at rest: terminal voltage == OCV, so no IR compensation is needed
    soc_ocv = soc_from_ocv(p.ocv_table, mean_mv / 1000.0);
    delta = soc_ocv - soc;
    delta = max(-p.rest_cap_pct, min(p.rest_cap_pct, delta));
    soc = soc + p.rest_gain * delta;
    anchored = true;
end
