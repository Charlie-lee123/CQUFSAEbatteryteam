function soc_reset()
%SOC_RESET  Clear all persistent state of the SOC estimator and its sub-blocks.
%Call this before a fresh simulation run so the estimator does not carry over
%SOC / drift / history from a previous run.
    clear soc_step
    clear rest_anchor
    clear mode_classify
end
