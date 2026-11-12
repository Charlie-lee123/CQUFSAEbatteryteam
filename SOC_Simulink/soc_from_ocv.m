function soc = soc_from_ocv(tbl, ocv)
%SOC_FROM_OCV  OCV(V) -> SOC(%). Reverse lookup, no extrapolation.
%   soc = soc_from_ocv(tbl, ocv)
%   tbl is [SOC, OCV] (see soc_params). Values outside the table are
%   clamped to the endpoint SOC (0 / 100).
    if ocv <= tbl(1,2)
        soc = tbl(1,1); return;
    end
    if ocv >= tbl(end,2)
        soc = tbl(end,1); return;
    end
    for i = 2:size(tbl,1)
        x1 = tbl(i,2); y1 = tbl(i,1);
        if ocv <= x1
            x0 = tbl(i-1,2); y0 = tbl(i-1,1);
            if x1 == x0
                soc = y1; return;
            end
            soc = y0 + (y1 - y0) * (ocv - x0) / (x1 - x0);
            return;
        end
    end
    soc = tbl(end,1);
end
