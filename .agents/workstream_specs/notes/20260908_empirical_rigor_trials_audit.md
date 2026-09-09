# Enduring Note: Empirical Rigor & Adversarial Verification in Demo UI Trials

**Date**: 2026-09-08  
**Author**: Antigravity  
**Status**: Pending  
**Related Spec**: [ws16_empirical_rigor_trials.md](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/ws16_empirical_rigor_trials.md)

## Context & Critical Findings
A rigorous scientific review of the three Demo UI trial doctests identified critical scientific integrity flaws:
1. **RAG Semantic Drift**: Retrievals frequently match generic glossary terms (e.g. airport firefighting) rather than relevant empirical literature chunks, while reports falsely claim "literature grounding."
2. **Fabricated Attribution**: The persona evaluated quantitative parameters (110W, 460W, 960Wh) not present in the model's text output.
3. **Inversion Bugs in Code Sandbox**: The Python script composed by the local model used `.idxmin()` on mission duration, recommending the worst-performing 101-minute configuration for a 120-minute mission requirement. The narrative rationalized this as a success.
4. **Circular Ontology Synthesis**: Grips stub elaboration merely echoed the prompt's 10-word focus hint in buzzwords without extracting physical domain parameters (frequency, dB loss, packet rates).
5. **Passive Branching**: Branch creation cloned database rows, but no alternative hypothesis was modeled inside the branch.

## Enduring Constraints for Future Work
- **Strict Verification Constraint**: Never allow the Researcher persona in any doctest or report to reference or evaluate quantitative numbers that do not appear verbatim in the recorded model token stream or sandbox execution logs.
- **Error Flagging Constraint**: When local models make logical or code errors, doctests must demonstrate active error detection: the persona must flag the discrepancy in the UI and execute a corrective turn.
- **Lineage Constraint**: All simulation numbers in Trial 3 must trace directly to empirical extraction in Trial 2 from Talavera et al. (2023) or Penders et al. (2011).
