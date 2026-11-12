function slope = slope_at(tbl, soc)
%SLOPE_AT  OCV slope at a given SOC, in mV/%. Used to weight voltage trust.
%   slope = slope_at(tbl, soc)
%   Plateau segments return a small slope; the knees (low/high SOC) return
%   a large slope. Clamped to the table range.
    x = max(tbl(1,1), min(tbl(end,1), soc));
    for i = 2:size(tbl,1)
        x1 = tbl(i,1); y1 = tbl(i,2);
        if x <= x1
            x0 = tbl(i-1,1); y0 = tbl(i-1,2);
            if x1 == x0
                slope = 0; return;
            end
            slope = (y1 - y0) * 1000.0 / (x1 - x0);
            return;
        end
    end
    slope = 0;
end
