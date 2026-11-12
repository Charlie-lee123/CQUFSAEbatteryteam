function mode = mode_classify(p, ts, mean_mv, i_eff, i_valid, contactor_open, bms_ok)
%MODE_CLASSIFY  Classify the pack as discharge / charge / idle / open / unknown.
%
%   mode = mode_classify(p, ts, mean_mv, i_eff, i_valid, contactor_open, bms_ok)
%
%   Priority (hardware constraint, DO NOT reorder):
%     1. mean_mv <= 0                              -> unknown
%     2. contactor_open                           -> open   (bus physically off)
%     3. ~bms_ok                                  -> unknown
%     4. valid current beyond threshold           -> discharge / charge (by sign)
%     5. long-window voltage trend (current-free) -> discharge / charge / idle
%   A hysteresis hold prevents flicker near thresholds. Uses a fixed-size
%   circular buffer for the voltage trend window. Mirrors soc_est.WorkModeClassifier.
    persistent mbuf mhead mcnt mfirst m_mode m_hold
    NBUF = 512;
    if isempty(mbuf)
        mbuf = zeros(NBUF, 2); mhead = 1; mcnt = 0; mfirst = 1;
        m_mode = 'unknown'; m_hold = 0;
    end

    if mean_mv <= 0
        m_mode = set_mode('unknown', ts, true); mode = m_mode; return;
    end
    if contactor_open
        m_mode = set_mode('open', ts, true); mode = m_mode; return;
    end
    if ~bms_ok
        m_mode = set_mode('unknown', ts, true); mode = m_mode; return;
    end
    if i_valid && abs(i_eff) > p.mode_i_min_a
        newmode = 'discharge'; if i_eff <= 0, newmode = 'charge'; end
        m_mode = set_mode(newmode, ts, false); mode = m_mode; return;
    end

    % ---- voltage-trend fallback (current missing / rail / tiny) ----
    cut = ts - p.mode_v_window_s;
    while mcnt > 0 && mbuf(mfirst, 1) < cut
        mfirst = mod(mfirst, NBUF) + 1;
        mcnt = mcnt - 1;
    end
    mbuf(mhead, :) = [ts, mean_mv];
    mhead = mod(mhead, NBUF) + 1;
    if mcnt < NBUF, mcnt = mcnt + 1; end

    if mcnt < 2
        m_mode = set_mode('idle', ts, false); mode = m_mode; return;
    end
    first_idx = mfirst;
    last_idx = mod(mhead - 2 + NBUF, NBUF) + 1;
    t0 = mbuf(first_idx, 1); v0 = mbuf(first_idx, 2);
    t1 = mbuf(last_idx, 1);  v1 = mbuf(last_idx, 2);
    span = t1 - t0;
    if span < p.mode_v_window_s * 0.5
        m_mode = set_mode('idle', ts, false); mode = m_mode; return;
    end
    delta = v1 - v0;
    if delta <= -p.mode_v_delta_mv
        m_mode = set_mode('discharge', ts, false);
    elseif delta >= p.mode_v_delta_mv
        m_mode = set_mode('charge', ts, false);
    else
        m_mode = set_mode('idle', ts, false);
    end
    mode = m_mode;

    % -------- nested helper (shares persistent state via closure) --------
    function m = set_mode(newmode, tsn, force)
        if ~force && ~strcmp(newmode, m_mode)
            % while in the hold window, don't downgrade to idle/open/unknown
            if (tsn < m_hold) && ismember(newmode, {'idle', 'open', 'unknown'})
                m = m_mode; return;
            end
        end
        m_mode = newmode;
        if ismember(newmode, {'discharge', 'charge'}) && isfinite(tsn)
            m_hold = tsn + p.mode_hold_s;
        end
        m = newmode;
    end
end
