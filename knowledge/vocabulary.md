# Word list of the knowledge cards

The only values allowed in a card's `infeed`, `outfeed` (the form, before the parentheses) and
`end_effectors` fields. Each line: `- <value>: <synonym>, <synonym>, ...` (French, German,
English). `catalog` filters match the value or any synonym; `dmx-docs knowledge check` rejects a
card with a value that is not here. Add a value here first, then use it in the cards.

Meanings agreed with the user (2026-10-16):
- "carton" alone can be a box or a case, so it is a synonym of neither: choose box (retail
  folding carton, "étui") or case (shipping case, "caisse").
- blister = plastic (thermoformed cavities); tray = cardboard or plastic tray ("barquette").
- flow wrapper chain = placing products into the infeed chain of a flow wrapper ("chaîne à
  taquets", bucket chain); the flowpack is then made by the wrapper.
- vacuum = gripping by vacuum ("vide"); suction cups ("ventouses") are the plastic parts of a
  vacuum end effector, so they are synonyms of vacuum.

## forms
- loose: vrac, en vrac, produit nu, produits nus, lose, unverpackt, bulk, unpacked
- flowpack: flow-pack, flow pack, flowpacks, sachet flowpack, schlauchbeutel, pochon, pochons
- pouch: sachet, sachets, beutel, sachet tenant debout, standbeutel, doypack
- tray: barquette, barquettes, plateau, trays, schale, tablett, cardboard tray, plateau carton
- box: boite, boîte, boites, boîtes, etui, étui, etuis, étuis, faltschachtel, carton box, folding box, folding carton
- case: caisse, caisses, caisse americaine, caisse américaine, shipping case, rsc, versandkarton
- wrap-around case: wrap-around, wraparound, wrap around, caisse wrap-around, wrap-around karton
- bottle: bouteille, bouteilles, flacon, flacons, flasche, flaschen
- can: boite de conserve, boîte de conserve, canette, dose, dosen, tin
- cup: pot, pots, gobelet, gobelets, becher, cups
- blister: blisters, plaquette, blisterpackung, blister plastique, plastic blister
- bag: sac, sacs, sachet vrac, tüte, bags
- flow wrapper chain: flowpack chain, flowwrapper chain, chaine flowpack, chaîne flowpack, chaine a taquets, chaîne à taquets, bucket chain, wrapper infeed chain, infeed chain

## end_effectors
- vacuum: vide, aspiration, vakuum, suction cups, suction cup, vacuum cups, ventouse, ventouses, prehenseur a ventouses, préhenseur à ventouses, sauger, saugnapf, vacuum gripper
- mechanical gripper: pince, pinces, prehenseur a doigts, préhenseur à doigts, doigts, greifer, mechanischer greifer, gripper, fingers
- needles: aiguilles, prehenseur a aiguilles, préhenseur à aiguilles, nadelgreifer, needle gripper
- magnets: aimant, aimants, prehenseur magnetique, préhenseur magnétique, magnetgreifer, magnetic gripper
- fork: fourche, fourches, pelle, gabel, shovel
- bernoulli: prehenseur bernoulli, préhenseur bernoulli, bernoulli-greifer, bernoulli gripper, sans contact, contactless
