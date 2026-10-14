## Revised future priorities

| Feature | Direction |
|---|---|
| Drift-aware/randomized ordering and reference zeroing | Next-round candidate; no implementation now |
| ARPLS and related baselines; fitting | Future modular analysis stages; await supplied implementations/design |
| Experimental recipes and user procedures | Later; no recipe/procedure runtime added |
| Background bank with dedicated reference tracking | Later; current scope is analysis loading and subtraction |
| Long-run temperature logging/mapping | After Lake Shore communication is verified |
| Absorbance/transmission | Lowest priority; current lab is PL and lacks the required measurement setup |
| Gated low-exposure timing development | Deferred; keep September 14 implementation pending suitable lab setup |

## Questions for the next lab check

If Andor still fails after this build starts correctly, what is the exact connection error and which configuration/DLL selection was used? Which physical detector/output path corresponds to detector 1 in the supplied coefficient filename? Are wavelengths in the coefficient file and lamp equation both in nm, as used in this implementation?
