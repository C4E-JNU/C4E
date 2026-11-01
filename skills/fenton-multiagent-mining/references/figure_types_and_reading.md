# Figure Types and Reading Caliber (survey reference)

> This file organizes the handling caliber for 107 figure types against the heterogeneous Fenton data-mining targets, based on a survey of 1,078 papers and 9,314 figures plus the current work-order and figure-extraction prompts.
> It is for documentation and review only and takes no part in runtime dispatch; the operative criteria are `backend/agents/fenton-workorder/system.md` and `backend/agents/fenton-image/system.md`.

## Inclusion criteria (identical to the work order, decided per subfigure)

1. **System match**: the catalyst is a solid metal site in a heterogeneous Fenton / Fenton-like system; anything outside that is not included.
2. **Incremental data**: the subfigure must contain extractable numbers or spectral key points that the text does not already give in full. If the text already states morphology, colour or structure and the figure merely illustrates or repeats it, it is not included.
3. **Readable**: complex 3D surfaces, complex molecular formulas and similar content that current vision models cannot read reliably are not included.
4. **Subfigure level**: one decision per subfigure; within one figure, some panels may be included and others not. Tables never enter the figure work order; MinerU parses them.

## Handling codes

| Code | Meaning | Reading rule |
|---|---|---|
| XY | Read the axes point by point (xy) | Read the axis label, unit and range first, then estimate every visible point (including start and end); multiple curves in one panel merge into one record. |
| B | Key-point reading (basic) | Take values from the key-point list for that type, numbers with units; spectra record peak positions/assignments; statistical plots record categories/values. |
| E | Not included | Never enters the work order: schematics, flow diagrams, photographs, molecular structures, 3D surfaces, or figures with no increment because the text already gives everything. |

Note: when a type contains both data panels and purely illustrative panels, the code gives the default caliber for the data panels; the work order decides at subfigure level.

## 1. Catalyst characterization (2,486 figures, 31 types)

| # | Figure type | Count | Handling | What the figure shows / key points |
|---|---|---|---|---|
| 1 | SEM | 272 | Key-point reading (basic) | Scanning electron microscopy: surface morphology of the catalyst (particle shape, size, agglomeration) |
| 2 | TEM | 233 | Key-point reading (basic) | Transmission electron microscopy: internal structure (lattice fringes, channels, nanoparticle distribution) |
| 3 | HRTEM | 103 | Key-point reading (basic) | High-resolution TEM at higher magnification: atomic-level lattice arrangement |
| 4 | HAADF-STEM | 39 | Key-point reading (basic) | High-angle annular dark-field STEM for single-atom imaging (bright dots = individual metal atoms) |
| 5 | SAED | 25 | Key-point reading (basic) | Selected-area electron diffraction: single-crystal vs. polycrystalline (spots or rings) |
| 6 | EDS / EDS mapping | 167 | Key-point reading (basic) | Energy-dispersive X-ray spectroscopy: elemental composition and distribution |
| 7 | STEM elemental mapping | 31 | Key-point reading (basic) | Elemental maps under STEM, colour-coded positions of each element on the catalyst |
| 8 | XRD | 242 | Key-point reading (basic) | X-ray diffraction: crystal structure and phase composition (which polymorph) |
| 9 | Small-angle XRD | 4 | Key-point reading (basic) | Small-angle X-ray scattering/diffraction: pore-channel ordering in mesoporous materials |
| 10 | XPS | 315 | Key-point reading (basic) | X-ray photoelectron spectroscopy: surface chemical states (e.g. Fe²⁺/Fe³⁺ ratio) |
| 11 | FTIR | 175 | Key-point reading (basic) | Fourier-transform infrared: functional groups (-OH, -COOH, metal-oxygen bonds) |
| 12 | Raman | 76 | Key-point reading (basic) | Raman spectroscopy: molecular vibrations, defect degree of carbon materials (D/G bands) |
| 13 | UV-Vis DRS | 17 | Key-point reading (basic) | UV-Vis diffuse reflectance: light absorption and band gap |
| 14 | Tauc plot | 8 | Key-point reading (basic) | Tauc plot: semiconductor band gap derived from UV-Vis DRS |
| 15 | PL | 13 | Key-point reading (basic) | Photoluminescence: recombination efficiency of photogenerated electron-hole pairs |
| 16 | BET | 105 | Key-point reading (basic) | N₂ adsorption-desorption: specific surface area |
| 17 | BJH | 26 | Key-point reading (basic) | Barrett-Joyner-Halenda method: pore-size distribution |
| 18 | VSM | 83 | Key-point reading (basic) | Vibrating-sample magnetometry: hysteresis loop, saturation magnetization (magnetic separability) |
| 19 | TGA | 30 | Key-point reading (basic) | Thermogravimetric analysis: thermal stability and organic content |
| 20 | DTG | 6 | Key-point reading (basic) | Derivative thermogravimetry: first derivative of TGA, clearer weight-loss stages |
| 21 | Zeta potential | 51 | Key-point reading (basic) | Zeta potential: surface charge (electrostatic interaction with pollutants) |
| 22 | H₂-TPR | 27 | Key-point reading (basic) | H₂ temperature-programmed reduction: reducibility (redox capacity) |
| 23 | NH₃-TPD | 6 | Key-point reading (basic) | NH₃ temperature-programmed desorption: number and strength of surface acid sites |
| 24 | Contact angle | 3 | Key-point reading (basic) | Contact-angle measurement: hydrophilicity/hydrophobicity of catalyst or membrane |
| 25 | CV | 35 | Key-point reading (basic) | Cyclic voltammetry: electrochemical activity, redox peaks |
| 26 | EIS | 38 | Key-point reading (basic) | Electrochemical impedance spectroscopy: charge-transfer resistance (electron-transfer efficiency) |
| 27 | i-t | 7 | XY point-by-point | Current-time curve: current response to additions |
| 28 | OCP | 15 | XY point-by-point | Open-circuit potential: equilibrium potential in solution |
| 29 | LSV | 23 | Key-point reading (basic) | Linear sweep voltammetry: current vs. potential (OER/HER activity) |
| 30 | Mott-Schottky | 6 | Key-point reading (basic) | Mott-Schottky plot: semiconductor type (n/p) and carrier concentration |
| 31 | KPFM | 2 | Key-point reading (basic) | Kelvin probe force microscopy: surface work function / potential distribution |

## 2. Degradation performance (3,973 figures, 34 types)

| # | Figure type | Count | Handling | What the figure shows / key points |
|---|---|---|---|---|
| 32 | Degradation curve | 360 | XY point-by-point | C/C₀ vs. time: residual pollutant concentration over time, degradation rate |
| 33 | UV-Vis over time | 126 | XY point-by-point | UV-Vis spectra over time: the characteristic absorption peak decays |
| 34 | Decolorization efficiency | 103 | Key-point reading (basic) | Decolorization percentage, colour removal (dye wastewater) |
| 35 | Multi-pollutant comparison | 68 | Key-point reading (basic) | Degradation compared across pollutants (which is easy, which is hard) |
| 36 | Absorption spectral evolution | 30 | Key-point reading (basic) | Full-spectrum absorbance evolution: formation and disappearance of intermediates |
| 37 | TOC | 210 | Key-point reading (basic) | Total organic carbon: degree of mineralization |
| 38 | COD | 205 | Key-point reading (basic) | Chemical oxygen demand: total organic matter (indirect contamination level) |
| 39 | BOD/COD | 35 | Key-point reading (basic) | BOD/COD ratio: biodegradability index (higher = more biodegradable) |
| 40 | TOC removal | 77 | Key-point reading (basic) | TOC removal percentage: mineralization extent |
| 41 | Mineralization efficiency | 20 | Key-point reading (basic) | Efficiency of conversion to CO₂ and H₂O |
| 42 | Intermediates | 58 | Key-point reading (basic) | Intermediate concentration profiles (GC-MS/HPLC): degradation pathway |
| 43 | Pseudo-first-order fit | 200 | XY point-by-point | ln(C/C₀) vs. time: rate constant k |
| 44 | k comparison | 41 | Key-point reading (basic) | Bar chart of rate constants under different conditions |
| 45 | Arrhenius | 34 | XY point-by-point | ln k vs. 1/T: activation energy Ea (temperature dependence) |
| 46 | Eyring | 6 | XY point-by-point | Eyring fit: activation enthalpy ΔH‡ and entropy ΔS‡ |
| 47 | Langmuir-Hinshelwood | 13 | XY point-by-point | L-H kinetic model: surface-reaction kinetics fit |
| 48 | Adsorption kinetics | 58 | XY point-by-point | Adsorbed amount vs. time: adsorption rate |
| 49 | Adsorption isotherm | 66 | XY point-by-point | Adsorbed amount vs. equilibrium concentration: capacity and isotherm type (Langmuir/Freundlich) |
| 50 | Adsorption vs. degradation | 36 | XY point-by-point | Contribution of adsorption alone vs. adsorption plus degradation |
| 51 | pH effect | 332 | XY point-by-point | Degradation efficiency vs. pH |
| 52 | H₂O₂ effect | 260 | XY point-by-point | Degradation efficiency vs. H₂O₂ concentration |
| 53 | Catalyst dosage | 238 | XY point-by-point | Degradation efficiency vs. catalyst dosage |
| 54 | Initial concentration | 179 | XY point-by-point | Efficiency/kinetics vs. initial pollutant concentration |
| 55 | Temperature | 154 | XY point-by-point | Efficiency/rate constant vs. reaction temperature |
| 56 | Anion effect | 137 | XY point-by-point | Inhibition or promotion by Cl⁻, NO₃⁻, SO₄²⁻ and other anions |
| 57 | HA effect | 57 | XY point-by-point | Effect of humic acid (natural organic matter) on degradation |
| 58 | Coexisting substances | 64 | XY point-by-point | Effect of other coexisting substances (heavy metals, surfactants, etc.) |
| 59 | Catalyst preparation parameters | 20 | XY point-by-point | Effect of calcination temperature, precursor ratio and other preparation conditions on activity |
| 60 | Ball-milling time | 5 | XY point-by-point | Effect of ball-milling time on performance |
| 61 | 3D surface + contour | 71 | Not included | 3D response surface plus contour plot: two-factor interaction on degradation |
| 62 | Perturbation plot | 8 | XY point-by-point | Sensitivity of the response to each factor (steeper = larger effect) |
| 63 | Predicted vs. actual | 40 | Key-point reading (basic) | Scatter of model-predicted vs. measured values: model accuracy |
| 64 | Ramps | 7 | XY point-by-point | Optimal-condition display (value range of each parameter) |
| 65 | EEM | 10 | Key-point reading (basic) | Excitation-emission matrix fluorescence: organic composition changes in wastewater |

## 3. Degradation mechanism (2,460 figures, 36 types)

| # | Figure type | Count | Handling | What the figure shows / key points |
|---|---|---|---|---|
| 66 | in situ Raman | 26 | Key-point reading (basic) | In situ Raman: real-time surface species during reaction |
| 67 | in situ ATR-FTIR | 15 | Key-point reading (basic) | In situ attenuated total reflectance IR: real-time adsorbates/intermediates |
| 68 | in situ pH | 5 | XY point-by-point | In situ pH monitoring during reaction |
| 69 | Quenching experiment | 258 | Key-point reading (basic) | Radical scavengers added (TBA/MeOH/p-BQ etc.) to identify the dominant radical |
| 70 | EPR radical spectrum | 252 | Key-point reading (basic) | Electron paramagnetic resonance: direct detection of ·OH, SO₄·⁻, O₂·⁻, ¹O₂ signals |
| 71 | TA fluorescence | 33 | Key-point reading (basic) | Terephthalic acid fluorescence: ·OH yield (TA + ·OH → highly fluorescent product) |
| 72 | PMSO probe | 31 | Key-point reading (basic) | Methyl phenyl sulfoxide probe: distinguish high-valent metal-oxo species (Fe(IV)=O) from radical pathways |
| 73 | DMSO oxidation | 7 | Key-point reading (basic) | Dimethyl sulfoxide oxidation: detect high-valent metal-oxo species |
| 74 | trans-stilbene oxidation | 6 | Key-point reading (basic) | trans-stilbene oxidation probe: distinguish ¹O₂ from radical pathways |
| 75 | ¹⁸O isotope labelling | 6 | Key-point reading (basic) | ¹⁸O-labelled H₂O₂ or O₂: trace the oxygen source |
| 76 | Mechanism schematic | 345 | Not included | Cartoon schematic of the whole catalytic degradation pathway |
| 77 | Degradation pathway diagram | 222 | Not included | Stepwise structural change of the pollutant (organics → small molecules → CO₂) |
| 78 | LSV | 43 | Key-point reading (basic) | Linear sweep voltammetry (mechanism section): electron-transfer pathway |
| 79 | EIS | 45 | Key-point reading (basic) | Electrochemical impedance (mechanism section): interfacial charge transfer |
| 80 | CV | 39 | Key-point reading (basic) | Cyclic voltammetry (mechanism section): redox mechanism |
| 81 | OCP | 17 | XY point-by-point | Open-circuit potential (mechanism section): reaction driving force |
| 82 | H₂-TPR | 10 | Key-point reading (basic) | H₂ temperature-programmed reduction (mechanism section): reducibility vs. activity |
| 83 | Cycling use | 273 | XY point-by-point | Degradation efficiency over repeated cycles (catalyst stability) |
| 84 | Metal leaching | 113 | Key-point reading (basic) | Dissolved metal-ion concentration during reaction (stability) |
| 85 | Post-reaction XRD | 118 | Key-point reading (basic) | XRD before vs. after reaction: crystal-structure change |
| 86 | Post-reaction XPS | 136 | Key-point reading (basic) | XPS before vs. after reaction: valence-state change |
| 87 | Post-reaction SEM/TEM | 77 | Key-point reading (basic) | Electron microscopy before vs. after reaction: morphology change |
| 88 | Post-reaction FTIR | 32 | Key-point reading (basic) | IR before vs. after reaction: surface functional-group change |
| 89 | Process flow diagram | 70 | Not included | Schematic of the experimental setup or process |
| 90 | Fixed-bed column | 34 | XY point-by-point | Fixed-bed reactor schematic or continuous-flow degradation curve |
| 91 | Fluidized-bed schematic | 6 | Not included | Fluidized-bed reactor structure |
| 92 | Membrane reactor | 15 | Not included | Membrane reactor structure, or membrane separation plus degradation |
| 93 | DC reactor | 1 | Not included | Dual-chamber reactor schematic |
| 94 | Two-chamber system | 9 | Not included | Two-compartment system (e.g. microbial fuel cell coupled to Fenton) |
| 95 | Toxicity plot | 35 | Key-point reading (basic) | Toxicity before vs. after degradation (luminescent bacteria inhibition, fish-embryo toxicity) |
| 96 | Photograph | 21 | Not included | Colour change of solutions, magnetic separation, etc. |
| 97 | DFT calculation | 124 | Key-point reading (basic) | Density functional theory: adsorption energy, density of states, charge-density difference, reaction barrier |
| 98 | Molecular dynamics | 12 | Key-point reading (basic) | MD simulation: molecular diffusion, mean square displacement (MSD) |
| 99 | Gibbs free energy | 21 | Key-point reading (basic) | Free-energy profile along the reaction path (rate-determining step) |
| 100 | Microbial community | 14 | Key-point reading (basic) | Community composition (phylum/genus) for biological treatment |
| 101 | 2D-COS | 2 | Key-point reading (basic) | Two-dimensional correlation spectroscopy: time order of FTIR signal evolution |

## 4. Data processing and supporting information (395 figures, 6 types)

| # | Figure type | Count | Handling | What the figure shows / key points |
|---|---|---|---|---|
| 102 | Predicted vs. actual | 127 | Key-point reading (basic) | Scatter of RSM or neural-network predictions vs. experiments: model accuracy |
| 103 | Residual analysis | 48 | Key-point reading (basic) | Normal-probability and distribution plots of residuals: model assumptions |
| 104 | ANFIS training curve | 16 | XY point-by-point | Training-error convergence of an adaptive neuro-fuzzy inference system |
| 105 | Error histogram | 17 | Key-point reading (basic) | Distribution of neural-network prediction errors |
| 106 | Molecular structure | 89 | Not included | Chemical structure of the pollutant (illustrative, not mechanistic) |
| 107 | ANN structure | 4 | Not included | Artificial neural network architecture schematic (input-hidden-output) |
