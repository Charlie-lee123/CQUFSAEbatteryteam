function build_soc_model()
%BUILD_SOC_MODEL  Assemble a Simulink model that runs soc_step().
%
%   >> build_soc_model
%
% Creates 'SOC_estimation.slx' next to this script, with a MATLAB Function
% block (calling soc_step) wired to demo sources and scopes. Open it and press
% Run; SOC and pack voltage appear on the scopes. Replace the Constant/Clock
% sources with your real CAN/serial feed (same port order/sizes) to go live.
%
% NOTE: the MATLAB Function block is set to INTERPRETED execution so the full
% MATLAB feature set is available (no C-codegen restriction). If you later want
% to generate C code from the model, flip 'IsInterpreted' back to 'off' and
% verify the helper functions compile (see README_Simulink.md).

    here = fileparts(mfilename('fullpath'));
    addpath(here);
    soc_reset();   % start from a clean state

    mdl = 'SOC_estimation';
    if bdIsLoaded(mdl), close_system(mdl, 0); end
    if exist(fullfile(here, [mdl '.slx']), 'file')
        delete(fullfile(here, [mdl '.slx']));
    end
    new_system(mdl);
    open_system(mdl);

    % ---------- MATLAB Function block ----------
    blk = [mdl '/SOC_Estimator'];
    add_block('simulink/User-Defined Functions/MATLAB Function', blk, ...
              'Position', [360 140 520 240]);
    code = [ ...
        'function [soc, pack_v, soc_min, soc_max, i_eff, flag, mode] = fcn(...\n' ...
        '        cells_mv, temps_c, i_meas, ts, bad_mask, contactor_open, bms_ok)\n' ...
        '    [soc, pack_v, soc_min, soc_max, i_eff, flag, mode] = ...\n' ...
        '        soc_step(cells_mv, temps_c, i_meas, ts, bad_mask, contactor_open, bms_ok);\n' ...
        'end\n' ];
    set_param(blk, 'MATLABFunction', code);
    try
        set_param(blk, 'IsInterpreted', 'on');   % robust for full MATLAB
    catch
        try, set_param(blk, 'InterpretedExecution', 'on'); end
    end

    % ---------- demo input sources ----------
    add_block('simulink/Sources/Constant', [mdl '/cells'], 'Position', [80 120 150 150]);
    set_param([mdl '/cells'], 'Value', '3700*ones(125,1)');

    add_block('simulink/Sources/Constant', [mdl '/temps'], 'Position', [80 200 150 230]);
    set_param([mdl '/temps'], 'Value', '40*ones(40,1)');

    add_block('simulink/Sources/Constant', [mdl '/i_meas'], 'Position', [80 280 150 310]);
    set_param([mdl '/i_meas'], 'Value', '120');   % 120 A discharge

    add_block('simulink/Sources/Clock', [mdl '/ts'], 'Position', [80 360 110 390]);

    add_block('simulink/Sources/Constant', [mdl '/bad_mask'], 'Position', [80 420 150 450]);
    set_param([mdl '/bad_mask'], 'Value', 'false(125,1)');

    add_block('simulink/Sources/Constant', [mdl '/contactor_open'], 'Position', [80 490 150 520]);
    set_param([mdl '/contactor_open'], 'Value', 'false');

    add_block('simulink/Sources/Constant', [mdl '/bms_ok'], 'Position', [80 560 150 590]);
    set_param([mdl '/bms_ok'], 'Value', 'true');

    % ---------- outputs: scopes + displays ----------
    add_block('simulink/Sinks/Scope', [mdl '/SOC_Scope'], 'Position', [640 140 670 170]);
    add_block('simulink/Sinks/Scope', [mdl '/Detail_Scope'], 'Position', [640 220 670 250]);
    add_block('simulink/Sinks/Display', [mdl '/flag_disp'], 'Position', [640 320 720 360]);
    add_block('simulink/Sinks/Display', [mdl '/mode_disp'], 'Position', [640 380 720 420]);

    % ---------- wiring (port order matches soc_step signature) ----------
    add_line(mdl, 'cells/1',        'SOC_Estimator/1');
    add_line(mdl, 'temps/1',        'SOC_Estimator/2');
    add_line(mdl, 'i_meas/1',       'SOC_Estimator/3');
    add_line(mdl, 'ts/1',           'SOC_Estimator/4');
    add_line(mdl, 'bad_mask/1',     'SOC_Estimator/5');
    add_line(mdl, 'contactor_open/1','SOC_Estimator/6');
    add_line(mdl, 'bms_ok/1',       'SOC_Estimator/7');

    add_line(mdl, 'SOC_Estimator/1', 'SOC_Scope/1');     % soc
    add_line(mdl, 'SOC_Estimator/2', 'Detail_Scope/1');  % pack_v
    add_line(mdl, 'SOC_Estimator/3', 'Detail_Scope/2');  % soc_min
    add_line(mdl, 'SOC_Estimator/4', 'Detail_Scope/3');  % soc_max
    add_line(mdl, 'SOC_Estimator/5', 'Detail_Scope/4');  % i_eff
    add_line(mdl, 'SOC_Estimator/6', 'flag_disp/1');     % flag
    add_line(mdl, 'SOC_Estimator/7', 'mode_disp/1');     % mode

    % ---------- model configuration ----------
    set_param(mdl, 'SolverType', 'Fixed-step');
    set_param(mdl, 'SolverName', 'FixedStepDiscrete');
    set_param(mdl, 'FixedStep', '1');     % one block call = one sampling round (~1-2 s)
    set_param(mdl, 'StopTime', '600');

    save_system(mdl, fullfile(here, [mdl '.slx']));
    fprintf('Model built: %s\n', fullfile(here, [mdl '.slx']));
    fprintf('Run it, then watch SOC_Scope (SOC%%) and Detail_Scope (pack V, min/max, current).\n');
end
