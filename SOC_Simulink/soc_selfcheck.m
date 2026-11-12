function failures = soc_selfcheck()
%SOC_SELFCHECK  Verify the MATLAB port against the Python reference values
%(computed from soc_est.py, SerialAssistant V2.7). Returns the number of failed
%checks. Run:  >> soc_selfcheck
    if nargout == 0, failures = soc_selfcheck(); fprintf('FAILURES = %d\n', failures); return; end
    failures = 0;
    p = soc_params();
    tbl = p.ocv_table;

    fprintf('SOC MATLAB self-check (reference: soc_est.py V2.7)\n');

    % ---- OCV table lookups (deterministic) ----
    failures = ck(failures, 'ocv_from_soc(0)  ',   ocv_from_soc(tbl, 0),   3.0818);
    failures = ck(failures, 'ocv_from_soc(50) ',   ocv_from_soc(tbl, 50),  3.903804);
    failures = ck(failures, 'ocv_from_soc(100)',   ocv_from_soc(tbl, 100), 4.4981);
    failures = ck(failures, 'soc_from_ocv(3.0818)', soc_from_ocv(tbl, 3.0818), 0);
    failures = ck(failures, 'soc_from_ocv(3.9038)', soc_from_ocv(tbl, 3.9038), 49.9996);
    failures = ck(failures, 'soc_from_ocv(4.4981)', soc_from_ocv(tbl, 4.4981), 100);
    failures = ck(failures, 'slope_at(2)   ',   slope_at(tbl, 2),   147.6575);
    failures = ck(failures, 'slope_at(20)  ',   slope_at(tbl, 20),  4.0949);
    failures = ck(failures, 'slope_at(50)  ',   slope_at(tbl, 50),  9.2738);
    failures = ck(failures, 'kappa_at(2)   ',   kappa_at(p, tbl, 2),   1.0);
    failures = ck(failures, 'kappa_at(20)  ',   kappa_at(p, tbl, 20),  0.4095);
    failures = ck(failures, 'kappa_at(50)  ',   kappa_at(p, tbl, 50),  0.9274);

    % ---- total pack voltage ----
    failures = ck(failures, 'pack_voltage(3700,125)', pack_voltage(3700, 125), 462.5);

    % ---- cleaning: 123 cells @3700, one @0 (missing), one @5000 (over-range) ----
    cells = 3700 * ones(125, 1);
    cells(11) = 0; cells(41) = 5000;
    cr = clean_cells(p, cells, false(125, 1));
    failures = ck(failures, 'clean.mean_mv',  cr.mean_mv, 3700);
    failures = ck(failures, 'clean.median',   cr.median_mv, 3700);
    failures = ck(failures, 'clean.gap',      cr.gap_mv, 0);
    failures = ck(failures, 'clean.ok',       double(cr.ok), 1);
    failures = ck(failures, 'clean.module_offset', double(cr.module_offset), 0);
    failures = ck(failures, 'clean.min_mv',   cr.min_mv, 3700);
    failures = ck(failures, 'clean.max_mv',   cr.max_mv, 3700);
    failures = ck(failures, 'clean.pack_mv',  cr.pack_mv, 462500);
    if ~isequal(sort(cr.bad_idx), [11; 41])
        failures = failures + 1;
        fprintf('  FAIL clean.bad_idx = [%s] (expected [11;41])\n', sprintf('%d ', cr.bad_idx'));
    else
        fprintf('  ok  clean.bad_idx = [11;41]\n');
    end

    % ---- estimator cold start at 3800 mV, no current, contactor open ----
    soc_reset();
    [s1, pv1, ~, ~, ie1, fl1, md1] = soc_step(3800*ones(125,1), 40*ones(40,1), ...
        NaN, 0, false(125,1), true, true);
    failures = ck(failures, 'cold-start soc', s1, 37.001);
    failures = ck(failures, 'cold-start pack_v', pv1, 475.0);
    failures = ck(failures, 'cold-start i_eff', ie1, 0);
    if ~strcmp(md1, 'open')
        failures = failures + 1; fprintf('  FAIL cold-start mode = %s (expected open)\n', md1); end
    % second identical round: must stay put (no current -> no integration)
    [s2, ~, ~, ~, ~, ~, ~] = soc_step(3800*ones(125,1), 40*ones(40,1), ...
        NaN, 2, false(125,1), true, true);
    failures = ck(failures, 'steady-state soc', s2, s1);

    % ---- Ah integration direction: discharge must yield LOWER SOC than a
    %      rest at the identical voltage (both get the same OCV pullback, but
    %      only discharge subtracts the Ah term). ----
    %  rest trajectory: contactor open, no current, same voltages
    soc_reset();
    soc_step(3800*ones(125,1), 40*ones(40,1), NaN, 0, false(125,1), true, true);
    [s_rest, ~, ~, ~, ieR, ~, mdR] = soc_step(3782*ones(125,1), 40*ones(40,1), ...
        NaN, 2, false(125,1), true, true);
    %  discharge trajectory: 120 A for 2 s, same voltages
    soc_reset();
    soc_step(3800*ones(125,1), 40*ones(40,1), NaN, 0, false(125,1), true, true);
    [s_dis, ~, ~, ~, ie3, ~, md3] = soc_step(3782*ones(125,1), 40*ones(40,1), ...
        120.0, 2, false(125,1), false, true);
    failures = ck(failures, 'discharge i_eff', ie3, 120.0);
    if ~strcmp(md3, 'discharge')
        failures = failures + 1; fprintf('  FAIL discharge mode = %s\n', md3); end
    if ~strcmp(mdR, 'open')
        failures = failures + 1; fprintf('  FAIL rest mode = %s (expected open)\n', mdR); end
    if s_dis >= s_rest
        failures = failures + 1;
        fprintf('  FAIL Ah integration: discharge soc %.3f >= rest soc %.3f\n', s_dis, s_rest);
    else
        fprintf('  ok  Ah integration: discharge(%.3f) < rest(%.3f)\n', s_dis, s_rest);
    end

    if failures == 0
        fprintf('\nALL CHECKS PASSED.\n');
    else
        fprintf('\n%d CHECK(S) FAILED.\n', failures);
    end
end

function f = ck(f, name, got, exp)
    tol = 1e-3;
    if isnan(got) || abs(got - exp) > tol
        f = f + 1;
        fprintf('  FAIL %-22s got=%g exp=%g\n', name, got, exp);
    else
        fprintf('  ok  %-22s = %g\n', name, got);
    end
end
