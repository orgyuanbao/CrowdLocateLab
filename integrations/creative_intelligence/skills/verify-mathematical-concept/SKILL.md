---
name: verify-mathematical-concept
description: Challenge a mathematical claim or method from the current localization run using proof checks, counterexamples, and reproducible numerical verification.
---

This Skill is authored by the CrowdLocateLab integration. The referenced upstream commit contains no implementation of this Skill; the name matches the upstream controller's expected discovery path.

Read the exact candidate claim and its assumptions from saved research. Verify the quantifiers, dimensions, equivalence steps, boundary cases, and whether local statements have accidentally become global claims. Specifically examine collinear geometry, insufficient observations, rank deficiency, degenerate timestamps, and nuisance-parameter confounding when relevant.

Attempt to falsify the claim before summarizing supporting evidence. Reproduce relevant experiments with a separate implementation or independent numerical identity where feasible. Record the commands and observed outputs, including unsuccessful checks. A second prompt to the same model is a review pass, not an independent human referee or formal proof checker.

Write a verification note under the current run directory that separates confirmed derivation steps, numerical evidence, counterexamples, limitations, and open obligations. Weaken or reject a claim when the evidence warrants it. End with the required upstream status JSON; do not declare the original industrial localization problem solved from synthetic experiments.
