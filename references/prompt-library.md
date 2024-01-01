# ARGUS Prompt Library

> Read me when: entering NLM Phase 1b (prompt design) — use these examples as quality anchors, then design topic-specific prompts from scratch. Do NOT copy-paste.

Source: TOOL-SOP.md §2.0.2

---

## Core Principle

**Prompts must be project-specific — never template copies.** probe.py Phase 1b requires fresh design every time.
This file stores **passing examples** as quality anchors for rubric self-checks.

---

## 5-Item Rubric Quality Gate

Every prompt must pass all 5 items. Any prompt that fails rubric is blocked from firing an NLM report.

| # | Rubric | Anti-pattern | Correct pattern |
|---|--------|-------------|-----------------|
| 1 | **Specify an analytical framework** | "Give me an overview of {domain}" | "Apply Porter's Five Forces to {domain} in {geography}. For each force, quantify with sources < 3 years old." |
| 2 | **Quantification requirements** | "Provide a market size estimate" | "Estimate TAM with 3 different methodologies (top-down / bottom-up / analogue). Provide a point estimate + 70% confidence interval + key assumptions." |
| 3 | **Output format directive** | "Detailed analysis" | "Output: (a) 3-column comparison table, (b) 1 decision matrix, (c) 1 numeric estimation box with source-by-source breakdown, (d) TL;DR ≤ 100 words at top." |
| 4 | **Anti-hallucination clause** | (none) | "If any claim lacks a source, label it 'UNVERIFIED'. If data comes from fewer than N sources, label confidence 'LOW'. Do NOT fabricate numbers." |
| 5 | **Aligned with target_decision** | Generic dimensions | "End with: 'How does this impact {specific decision}? Concrete Y1/Y2/Y3 action items with cost × reward.'" |

---

## Rubric Self-Check (Phase 1b internal validation)

```python
def passes_rubric(prompt: str) -> bool:
    checks = [
        any(fw in prompt for fw in ["Porter", "PESTEL", "BMC", "TAM", "Unit Econ", "Price Discrimination", "SWOT"]),  # R1
        any(q in prompt for q in ["confidence interval", "methodology", "point estimate", "P25", "P75", "median"]),    # R2
        any(f in prompt for f in ["Output:", "table", "matrix", "TL;DR", "format"]),                                   # R3
        any(h in prompt for h in ["UNVERIFIED", "fabricate", "label confidence", "cite"]),                            # R4
        any(d in prompt for d in ["decision", "action items", "Y1", "Y2", "impact"]),                                 # R5
    ]
    return all(checks)
```

---

## Passing Examples

### Example 1: Pricing Matrix (AI Dev Tooling Competitive Landscape)

**Dimension**: pricing_matrix  
**Framework**: Price Discrimination Theory + Channel Economics

```
Map the pricing landscape for self-hosted vs SaaS vs managed AI dev tools
across US West Coast / New York / Austin markets in 2024–2026.

Required output:
(a) Matrix table: [plan tier × team size × deployment model × region] with median + P25–P75 range
(b) Identify 3 'pricing anchors' (market-setting vendors) with evidence
(c) For each region, estimate price-elasticity indicators (e.g., seat-count premium, annual discount rate)
(d) Confidence tier per row (H/M/L) based on source count
(e) TL;DR ≤ 100 words at top

Evidence: cite ≥ 8 sources < 3 years old. For each number, show source-by-source derivation.
Forbidden: averaging across regions; 'ranges may vary'; UNVERIFIED numbers without explicit label.

Decision impact: How does this pricing landscape affect a new entrant's
Y1 revenue projection assuming N customers/month × $X mean ACV?
Give a Y1 gross revenue interval with underlying assumptions.
```

**Rubric pass verification**: R1 Price Discrimination Theory ✅ / R2 P25–P75 + median ✅ / R3 matrix table + TL;DR ✅ / R4 UNVERIFIED label ✅ / R5 Y1 decision impact ✅

---

### Example 2: TAM Three-Method Estimation (Open-Source LLM Orchestration)

**Dimension**: tam_estimation  
**Framework**: TAM-SAM-SOM + Bottom-up / Top-down / Analogue

```
Estimate the Total Addressable Market (TAM) for open-source LLM orchestration frameworks
in the United States for 2025–2027.

Required methodology:
(a) Top-down: Start from total US developer tooling spend → AI tooling % → orchestration layer share
    - Source each percentage split separately; label each with confidence (H/M/L)
(b) Bottom-up: Estimate from # of dev teams × AI tooling adoption rate × seat price
    - Show: source for dev team population, adoption rate distribution, price range
(c) Analogue: Compare to analogous open-source infrastructure markets (e.g., container orchestration)
    - Identify 2–3 analogues with penetration rates and growth trajectory

Cross-validation: Where do the 3 methods diverge? What assumption drives the gap?
Output: Single table — [method × TAM estimate × 70% confidence interval × key assumptions]
        plus 1 paragraph explaining why estimates diverge.

Anti-hallucination: Label any number without a cited source as UNVERIFIED.
Do NOT average the 3 estimates without explaining the weighting rationale.

Decision impact: Given this TAM, is a $1M ARR Y3 target for a solo operator realistic?
Show the implied market share % and comparable solo-operator benchmarks.
```

**Rubric pass verification**: R1 TAM-SAM-SOM + 3 methodologies ✅ / R2 confidence interval + point estimates ✅ / R3 table + cross-validation paragraph ✅ / R4 UNVERIFIED label + no unweighted averaging ✅ / R5 $1M ARR Y3 decision ✅

---

### Example 3: Developer Productivity SaaS Competitive Moat Analysis

**Dimension**: ai_leverage_differentiation  
**Framework**: Competitive Moat Analysis + Jobs-to-be-Done

```
Analyze how AI capabilities (code completion, automated review, CI/CD intelligence, incident triage)
create differentiation for developer productivity SaaS vendors in the US market (2024–2026).

Required output:
(a) 3-column comparison table: [AI capability × current adoption % × moat duration estimate]
    - Adoption % must cite a survey or industry source; label UNVERIFIED if estimated
(b) Jobs-to-be-Done map: Top 5 developer pain points + which AI tools address each
    - Source: practitioner forums, GitHub Discussions, industry reports
(c) Decision matrix: [AI investment × cost × time-to-implement × revenue uplift evidence]
(d) Identify 1–2 'contrarian' angles: where AI adoption is overhyped vs. where it is underused

Confidence tiers: H = cited industry study / M = practitioner testimony / L = inference
TL;DR ≤ 80 words at top.

Decision impact: For a new entrant launching in 2026, which 2–3 AI capabilities
deliver the highest Y1 ROI? Rank by (revenue_impact / implementation_cost) ratio
with source-backed assumptions.
```

**Rubric pass verification**: R1 Competitive Moat + JTBD ✅ / R2 adoption %, revenue uplift evidence ✅ / R3 3-column table + decision matrix + TL;DR ✅ / R4 UNVERIFIED label + confidence tiers ✅ / R5 Y1 ROI ranking decision ✅

---

## Anti-Pattern Checklist (these prompts are rejected outright — do not send to NLM)

| Anti-pattern | Problem | Correct approach |
|-------------|---------|-----------------|
| "Give me an overview of {domain}" | No framework; output will be a generic summary | Specify Porter / PESTEL / BMC / etc. |
| "List 5 key points" | No quantification, no format spec, no decision orientation | Specify table + decision matrix + target_decision |
| "Analyze the competitive landscape in detail" | "Detailed" is not a format directive | Specify concrete output structure (a)(b)(c) |
| "What are the latest trends" | No time range, no source requirements | Use `< 3 years old` + `cite ≥ 5 sources/section` |
| "Summarize the X industry for me" | No framework; NLM will return a generic overview | Apply a specific framework + all 5 rubric items |
| Generic template copy-paste | Not aligned with the actual topic | Phase 1b requires `design_prompts(topic, target_decision, ...)` every time |

---

## Phase 1b Design Workflow

```python
def design_prompts(topic, target_decision, constraints, personal_context):
    # Step 1: identify required frameworks
    frameworks = select_frameworks_for(topic, target_decision)
    # Reference: Porter / PESTEL / BMC / Unit Econ / TAM-SAM-SOM / Price Discrimination / JTBD

    # Step 2: generate 1–2 prompts per framework, embedding all 5 rubric items
    prompts = []
    for fw in frameworks:
        p = compose_prompt(
            framework=fw,
            topic=topic,
            geography=constraints.geography,
            target_decision=target_decision,
            evidence_requirements="cite ≥5 sources/section with URLs",
            format_spec="(a) table (b) matrix (c) TL;DR",
            anti_hallucination_clause="label UNVERIFIED + no fabricated numbers",
        )
        assert passes_rubric(p), f"Prompt {p.dim} failed rubric"
        prompts.append(p)

    return prompts  # → prompts.json → User Review Gate (Phase 1.5)
```

**Output**: `research-raw/<topic>/prompts.json`  
**Next step**: User approval (Phase 1.5) → ✅ then probe.py proceeds to Phase 2
