function k = kappa_at(p, tbl, soc)
%KAPPA_AT  Voltage-trust weight kappa in [kappa_lo, kappa_hi].
%   k = kappa_at(p, tbl, soc)
%   A steep OCV slope means voltage is informative -> trust it (kappa->1).
%   A plateau means voltage carries almost no SOC information -> ignore it
%   (kappa->kappa_lo). Mirrors soc_est.kappa_at.
    s = slope_at(tbl, soc);
    k = s / p.kappa_slope_ref;
    k = max(p.kappa_lo, min(p.kappa_hi, k));
end
