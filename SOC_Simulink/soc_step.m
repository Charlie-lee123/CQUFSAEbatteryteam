function [soc, pack_v, soc_min, soc_max, i_eff, flag, mode] = ...
    soc_step(cells_mv, temps_c, i_meas, ts, bad_mask, contactor_open, bms_ok)
%SOC_STEP  One round of E74 SOC + pack-voltage estimation. Stateful: keeps the
%Ah integral, zero-drift estimate, and rest/mode history between calls.
%
%   [soc, pack_v, soc_min, soc_max, i_eff, flag, mode] = ...
%       soc_step(cells_mv, temps_c, i_meas, ts, bad_mask, contactor_open, bms_ok)
%
%   INPUTS  (all scalar unless noted; sizes fixed for the E74 pack)
%     cells_mv     125x1 double   cell terminal voltages (mV); NaN = missing
%     temps_c       40x1 double   module NTC temperatures (C); median is used
%     i_meas        scalar double bus current reading (A); NaN = no field
%     ts            scalar double timestamp (s, monotonic); NaN = ignore time
%     bad_mask      125x1 logical device-flagged bad cells ('X')
%     contactor_open scalar logical  main contactor fully open (bus off)
%     bms_ok        scalar logical  BMS healthy
%   OUTPUTS
%     soc           double    state-of-charge, 0..100 (%)
%     pack_v        double    total pack voltage (V)
%     soc_min/max   double    weakest/strongest cell SOC via OCV (0..100)
%     i_eff         double    drift-corrected current, discharge positive (A)
%     flag          char     '' | 'NO_DATA' | 'WEAK' | 'RESIDUAL' | 'MODULE'
%     mode          char     'discharge' | 'charge' | 'idle' | 'open' | 'unknown'
%
%   Algorithm (ported from soc_est.py): Ah integral (skeleton) + kappa-weighted
%   OCV pullback + rest anchoring, with online zero-drift learning. The 4-state
%   EKF branch is intentionally NOT included -- the production path uses the
%   fixed-gain fusion because E74's sampling jitter (0.9~2.5 s) and current
%   drift make a full EKF diverge.
%
%   Reset all internal state with soc_reset().

    p = soc_params();

    persistent P_soc P_known P_bias P_last_ts
    if isempty(P_soc)
        P_soc = 50.0; P_known = false; P_bias = 0.0; P_last_ts = NaN;
    end

    % ---- argument defaults (so it is also callable from the command line) ----
    if nargin < 7 || isempty(bms_ok),            bms_ok = true;        end
    if nargin < 6 || isempty(contactor_open),    contactor_open = false; end
    if nargin < 5 || isempty(bad_mask),          bad_mask = false(p.n_cells, 1); end
    if nargin < 4 || isempty(ts) || ~isfinite(ts), ts_in = NaN; else ts_in = ts; end
    if nargin < 3 || isempty(i_meas),            i_meas_in = NaN; else i_meas_in = i_meas; end
    if nargin < 2 || isempty(temps_c),           temps_c_in = 20; else temps_c_in = temps_c; end

    cr = clean_cells(p, cells_mv, bad_mask);

    if ~cr.ok
        % not enough good cells: keep last SOC, report pack estimate, flag it
        if cr.mean_mv <= 0
            flag = 'NO_DATA';
        else
            flag = 'WEAK';
        end
        soc = P_soc;
        pack_v = cr.pack_mv / 1000.0;
        soc_min = NaN; soc_max = NaN;
        i_eff = 0;
        mode = mode_classify(p, ts_in, cr.mean_mv, 0, false, contactor_open, bms_ok);
        return;
    end

    % ---- current: drift + sign ----
    [i_eff, i_valid, P_bias] = compute_current(p, i_meas_in, contactor_open, bms_ok, P_bias);

    % ---- time step ----
    gap = NaN; dt = 0.0;
    if isfinite(ts_in)
        if isfinite(P_last_ts)
            gap = ts_in - P_last_ts;
            dt = gap;
            if dt < 0 || dt > p.dt_max_s
                dt = 0.0;                  % reconnect/pause -> no such interval
            end
        end
        P_last_ts = ts_in;
    end

    temp_c = median_temp(temps_c_in);

    anchored = false;
    initialised = false;

    if ~P_known
        % cold start: this round's (IR-compensated) terminal voltage -> OCV
        P_soc = soc_from_ocv(p.ocv_table, ocv_estimate(p, cr.mean_mv, i_eff) / 1000.0);
        P_known = true;
        initialised = true;
    else
        % long silence => the integral is worthless; re-anchor from OCV
        if isfinite(gap) && gap > p.reinit_gap_s ...
                && (~i_valid || abs(i_eff) <= p.rest_i_max_a)
            P_soc = soc_from_ocv(p.ocv_table, ocv_estimate(p, cr.mean_mv, i_eff) / 1000.0);
            initialised = true;
        end
        if ~initialised
            % 1) Ah integration (skeleton)
            if i_valid && dt > 0
                P_soc = P_soc - i_eff * dt / 3600.0 / p.q_ah * 100.0;
            end
            % 2) voltage pullback (kappa weighted); skip if RESIDUAL
            if ~cr.residual
                ocv_v = ocv_estimate(p, cr.mean_mv, i_eff) / 1000.0;
                soc_ocv = soc_from_ocv(p.ocv_table, ocv_v);
                kk = kappa_at(p, p.ocv_table, P_soc) * p.k_volt;
                P_soc = P_soc + kk * (soc_ocv - P_soc);
            end
            % 3) rest anchor (the only strong correction)
            [P_soc, anchored] = rest_anchor(p, ts_in, cr.mean_mv, i_eff, i_valid, temp_c, P_soc);
        end
    end

    P_soc = max(0.0, min(100.0, P_soc));

    % ---- min / max from the TRUE extreme cells (OCV-inverted) ----
    ocv_bias_mv = (i_valid * i_eff) * p.r0_mohm;     % no EKF -> pol_mv = 0
    soc_min = soc_from_ocv(p.ocv_table, (cr.min_mv + ocv_bias_mv) / 1000.0);
    soc_max = soc_from_ocv(p.ocv_table, (cr.max_mv + ocv_bias_mv) / 1000.0);
    soc_min = max(0.0, min(100.0, soc_min));
    soc_max = max(0.0, min(100.0, soc_max));
    if soc_min > soc_max
        tmp = soc_min; soc_min = soc_max; soc_max = tmp;
    end

    mode = mode_classify(p, ts_in, cr.mean_mv, i_eff, i_valid, contactor_open, bms_ok);

    flag = '';
    if cr.residual
        flag = 'RESIDUAL';
    elseif cr.module_offset
        flag = 'MODULE';
    elseif cr.n_unusable > 0 && cr.n_unusable > (cr.n_raw / 4)
        flag = 'WEAK';
    end

    pack_v = cr.pack_mv / 1000.0;
    soc = P_soc;
end

% ---------------------------------------------------------------- locals

function ocv_mv = ocv_estimate(p, mean_mv, i_eff)
% Terminal voltage -> OCV (mV): add back the IR drop (OCV = V + I*R0).
    ocv_mv = mean_mv + i_eff * p.r0_mohm;
end

function t = median_temp(temps)
% Representative pack temperature (C): median of valid NTC readings.
% Bad points are dropped; if none, return 20 C (room temp, don't block SOC).
    vals = [];
    for k = 1:numel(temps)
        v = temps(k);
        if isfinite(v) && v >= -40.0 && v <= 125.0
            vals = [vals; v];        %#ok<AGROW>
        end
    end
    if isempty(vals)
        t = 20.0;
    else
        t = median(vals);
    end
end
