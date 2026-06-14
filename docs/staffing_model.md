# Staffing Optimisation Model

## Overview

The staffing model decides **which 30-minute slots each staff member works**, so that
customer demand is covered at minimum wage cost while respecting labour rules and
each employee's contracted hours.

It is formulated as an **Integer Linear Programme (ILP)** and solved with an
off-the-shelf solver (PuLP + CBC).

This document specifies the model. It is the reference for the implementation in
`src/` and should be kept in sync with the code.

---

## Why a time-slot model

An earlier design used named shifts (`dim_shift_windows`) with a per-shift minimum
staffing table. That approach cannot cleanly represent:

- **Split shifts** — a staff member working a morning block, clocking out, then
  returning for an evening block.
- **Flexible break placement** — breaks that may fall anywhere within a work block.

The time-slot model divides the operating day into uniform 30-minute slots and lets
the optimiser choose which slots each employee works. Labour rules (maximum work
without a break, guaranteed hours, budget) become **constraints** rather than
hard-coded data. 

---

## Assumptions

1. The operating day is divided into 30-minute slots. Shifts start and end on
   `:00` or `:30` boundaries.
2. The operating window is **10:00–01:00** (next day), confirmed from EDA — 30 slots
   per day.
3. A staff member may not work more than **6 continuous hours (12 slots)** without a
   break.
4. A break is exactly **30 minutes (one slot)**.
5. Staff **clock out during breaks** — a break slot does not count toward coverage or
   cost.
6. A staff member is present (working time **plus** break time) for at most **8 hours
   (a span of 16 slots)** in any single day.
7. Demand per slot (`d`) is derived from historical transaction volume (see
   `ref_demand_by_slot`), using a configurable bills-per-staff service rate.
8. The planning horizon is one **fortnight** (14 days). Guaranteed hours are defined
   per fortnight.
9. A staff member works at most one slot at a time (no double-booking).
10. A staff member works at most their **guaranteed hours plus a fixed overtime
    margin** $\Delta$ over the fortnight ($\Delta = 16$ h). This caps individual
    overtime and prevents the optimiser piling hours onto the cheapest staff.

---

## Sets and Indices

| Symbol | Meaning |
|---|---|
| $s \in S$ | Staff members |
| $t \in T$ | Time slots over the planning horizon (each day's 30-min slots across the fortnight) |
| $D$ | Set of days in the horizon ($\lvert D \rvert = 14$) |
| $T_g \subseteq T$ | The ordered slots within a single day $g \in D$ |

---

## Parameters

| Symbol | Meaning | Source |
|---|---|---|
| $d_t$ | Minimum staff required in slot $t$ | `ref_demand_by_slot` |
| $a_{s,t} \in \{0,1\}$ | 1 if staff $s$ is available in slot $t$ | `bridge_staff_availability` |
| $w_s$ | Hourly wage rate of staff $s$ | `dim_staff` |
| $h_s$ | Guaranteed hours per fortnight for staff $s$ | `dim_staff` |
| $\Delta$ | Overtime margin: max hours above guaranteed per fortnight ($\Delta = 16$) | model input |
| $L$ | Maximum continuous working slots before a break ($L = 12$) | labour rule |
| $M$ | Maximum daily span in slots, working + break ($M = 16$, i.e. 8 hours) | labour rule |
| $\tau = 0.5$ | Hours per slot | constant |

---

## Decision Variables

$$
x_{s,t} =
\begin{cases}
1 & \text{if staff } s \text{ works slot } t \\
0 & \text{otherwise}
\end{cases}
\qquad \forall s \in S,\; t \in T
$$

---

## Objective

Minimise total wage cost over the horizon:

$$
\min \; \sum_{s \in S} \sum_{t \in T} \; \tau \, w_s \, x_{s,t}
$$

---

## Constraints

**(1) Demand coverage** — every slot must be staffed to its minimum:

$$
\sum_{s \in S} x_{s,t} \;\ge\; d_t
\qquad \forall t \in T
$$

**(2) Availability** — staff can only be assigned to slots they are available for:

$$
x_{s,t} \;\le\; a_{s,t}
\qquad \forall s \in S,\; t \in T
$$

**(3) Maximum continuous work (break rule)** — within any window of $L+1$
consecutive slots in a day, at most $L$ may be worked, forcing at least one break
slot:

$$
\sum_{i=0}^{L} x_{s,\,t+i} \;\le\; L
\qquad \forall s \in S,\; \forall g \in D,\; \forall t \text{ such that } t, \dots, t+L \in T_g
$$

**(4) Maximum daily presence (8-hour day)** — a staff member's working time plus
break time within a single day may span no more than 8 hours. Equivalently, two slots
that are 8 hours or more apart in the same day cannot both be worked:

$$
x_{s,p} + x_{s,q} \;\le\; 1
\qquad \forall s \in S,\; \forall g \in D,\; \forall p, q \in T_g \text{ with } q - p \ge M
$$

This bounds the elapsed time between a staff member's first and last worked slot in a
day to at most $M = 16$ slots (8 hours), which includes any break slots falling within
that span.

**(5) Guaranteed hours** — each staff member works at least their contracted hours
over the fortnight:

$$
\sum_{t \in T} \tau \, x_{s,t} \;\ge\; h_s
\qquad \forall s \in S
$$

**(6) Maximum hours** — each staff member works at most their guaranteed hours plus
the overtime margin $\Delta$ over the fortnight:

$$
\sum_{t \in T} \tau \, x_{s,t} \;\le\; h_s + \Delta
\qquad \forall s \in S
$$

This caps individual overtime. Together with constraint (1), it means demand must be
coverable within everyone's capped hours — if not, the model is infeasible, signalling
that headcount or contracted hours need revisiting (see Notes).

**(7) Binary domain**:

$$
x_{s,t} \in \{0, 1\}
\qquad \forall s \in S,\; t \in T
$$

---

## Notes and Limitations

- **Feasibility tension.** Constraint (1) demands enough labour to cover every slot,
  while constraint (6) caps each person's hours at $h_s + \Delta$. If total capped
  capacity $\sum_s (h_s + \Delta)$ is below total demand, the model is infeasible — a
  useful signal that headcount or contracted hours need revisiting. (The earlier design
  used a budget cap here instead; that was removed in favour of reporting the
  minimum-cost schedule directly.)
- **Break length.** With 30-minute slots, constraint (3) forces exactly one 30-minute
  break per 6-hour block, matching assumption 4. A longer mandated break would require
  widening the window or adding a dedicated break-length constraint.
- **Split shifts vs. the 8-hour span.** Constraint (4) caps daily *presence* at 8
  hours, so it also limits how wide a split shift can be — a morning and an evening
  block more than 8 hours apart cannot both be worked. If wide split shifts should be
  allowed, replace (4) with a cap on daily *working* slots instead of daily span.
- **Service rate is a modelling choice.** $d_t$ depends on the assumed bills-per-staff
  service rate; results should be reported as sensitivity to this assumption, not as a
  single point answer.
- **Demand is predicted, not causal.** $d_t$ is derived from historical averages and
  assumes future demand resembles the past. It does not account for one-off events,
  promotions, or trend growth.
- **No skill differentiation (yet).** The current model treats all staff as
  interchangeable for coverage. Role-based coverage (e.g. at least one supervisor per
  slot) would add a constraint per role.
```
