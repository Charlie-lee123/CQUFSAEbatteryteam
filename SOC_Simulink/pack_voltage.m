function V = pack_voltage(mean_mv, n_cells)
%PACK_VOLTAGE  Total pack voltage (V) from the healthy mean cell voltage.
%   V = pack_voltage(mean_mv, n_cells)
%   Matches soc_est.CleanResult.pack_mv: because every bad cell is back-filled
%   with the healthy mean, the sum of the full string is exactly
%   mean_mv * n_cells. Converted from mV to V.
    V = mean_mv * n_cells / 1000.0;
end
