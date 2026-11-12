function [i_eff, i_valid, i_bias_new] = compute_current(p, raw, contactor_open, bms_ok, i_bias)
%COMPUTE_CURRENT  Effective current with the P1 hardware ordering and online
%zero-drift learning. Returns (i_eff [A, discharge positive], i_valid, new bias).
%
%   [i_eff, i_valid, i_bias_new] = compute_current(p, raw, contactor_open, bms_ok, i_bias)
%
%   Ordering (DO NOT reorder -- it is a deliberate hardware constraint):
%     1. contactor fully open AND bms_ok  => real current is 0, the reading is
%        pure zero-drift. This is the ONLY reliable time to learn the drift.
%     2. reading missing / rail value / |raw| > i_limit_a  => no current this frame
%        (do NOT integrate, do NOT clamp -- clamping would invent a number).
%     3. otherwise subtract the drift and apply the sign convention.
%
%   raw = NaN means "no current field this round" (i_valid = false).
    if isnan(raw)
        i_eff = 0; i_valid = false; i_bias_new = i_bias; return;
    end

    if contactor_open && bms_ok
        % P1: bus disconnected => true current is 0, reading == drift
        if isfinite(raw) && abs(raw) <= p.bias_sample_max_a
            i_bias_new = i_bias + p.bias_gain * (raw - i_bias);
            i_bias_new = max(-p.bias_limit_a, min(p.bias_limit_a, i_bias_new));
        else
            i_bias_new = i_bias;
        end
        i_eff = 0; i_valid = true; return;
    end

    if ~isfinite(raw) || abs(raw) > p.i_limit_a
        i_eff = 0; i_valid = false; i_bias_new = i_bias; return;
    end

    i_eff = (raw - i_bias) * p.i_sign;
    i_valid = true; i_bias_new = i_bias;
end
