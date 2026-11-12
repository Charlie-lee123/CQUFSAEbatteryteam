function ocv = ocv_from_soc(tbl, soc)
%OCV_FROM_SOC  SOC(%) -> OCV(V). Linear interpolation, no extrapolation.
%   ocv = ocv_from_soc(tbl, soc)
%   tbl is [SOC, OCV] (see soc_params). Values outside the table are
%   clamped to the endpoint OCV.
    if soc <= tbl(1,1)
        ocv = tbl(1,2); return;
    end
    if soc >= tbl(end,1)
        ocv = tbl(end,2); return;
    end
    for i = 2:size(tbl,1)
        x1 = tbl(i,1); y1 = tbl(i,2);
        if soc <= x1
            x0 = tbl(i-1,1); y0 = tbl(i-1,2);
            if x1 == x0
                ocv = y1; return;
            end
            ocv = y0 + (y1 - y0) * (soc - x0) / (x1 - x0);
            return;
        end
    end
    ocv = tbl(end,2);
end
