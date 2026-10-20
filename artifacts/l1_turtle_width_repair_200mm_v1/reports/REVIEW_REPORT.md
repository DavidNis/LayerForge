# Turtle retained-width repair at 200 mm

Status: READY_FOR_USER_TEST — fixed stack remains structurally unresolved.

| Back-to-front layer | Color | Before | Orphan cores | Hidden capacity px | Exact repair px | Visible proposal px | Proposal width |
|---:|---|---|---:|---:|---:|---:|---|
| 0 | #276DA3 | PASS | 0 | 0 | 0 | 0 | PASS |
| 1 | #4B98CF | PASS | 0 | 0 | 0 | 0 | PASS |
| 2 | #90C6E1 | PASS | 0 | 0 | 0 | 0 | PASS |
| 3 | #F1D748 | PASS | 0 | 0 | 0 | 0 | PASS |
| 4 | #F6F6F6 | CONFLICT_REQUIRES_USER_DECISION | 4 | 0 | 0 | 148 | PASS |
| 5 | #0E0F0D | CONFLICT_REQUIRES_USER_DECISION | 3 | 0 | 0 | 185 | PASS |
| 6 | #90C6E1 | CONFLICT_REQUIRES_USER_DECISION | 3 | 0 | 0 | 185 | PASS |
| 7 | #5DA146 | CONFLICT_REQUIRES_USER_DECISION | 36 | 0 | 0 | 709 | PASS |
| 8 | #986840 | CONFLICT_REQUIRES_USER_DECISION | 99 | 0 | 0 | 1271 | PASS |
| 9 | #17334B | CONFLICT_REQUIRES_USER_DECISION | 238 | 0 | 0 | 3670 | PASS |
| 10 | #0E0F0D | CONFLICT_REQUIRES_USER_DECISION | 238 | 0 | 0 | 3671 | PASS |
| 11 | #986840 | PASS | 0 | 0 | 0 | 0 | PASS |

Exact-preserving reconstruction changed pixels: 0.
Unapproved visible proposal changed pixels: 4075.
The proposal has zero orphan structural cores, but retains seven thin-decorative warnings; three visible components remain outside the conservative restored-support map. These are not retained-width failures under the unchanged validator and remain separate printability warnings.

The visible proposal proves the local widening operator can make the seven failing masks pass, but it is not an acceptable replacement because it changes the indexed artwork. A different region-to-depth assignment is required for an exact solution.
