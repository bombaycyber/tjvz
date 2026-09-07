"""The one tjvz model.

    score(item) = bias + sum( weight_i * feature_i(item) )

No plugin registry — the variation that matters is all in config
(schema/model-config.schema.json):

    scoring:   features {weight, params, pinned}, bias, stochastic
    learning:  utility, label.positive, trainable, regularization, adopt

`score` / `explain` need only `scoring`. `learn` needs `learning`; it fits a
regularised logistic regression (utility: log_odds) by Newton's method —
d ~ 7, a dozen iterations, pure Python, no numpy. Weights shrink toward the
config weights (not 0); lambda is annealed by event count. A fit is
*adopted* only if it beats the config weights on k-fold CV log-loss and the
label counts clear the gates — `learn()` reports that, it does not decide.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# promote is the event verb; stack is the resulting location — same label.
_ALIAS = {"promote": "stack"}
_EPS = 1e-12


# --- tiny dense linear algebra (d ~ 7) --------------------------------------

def _solve(A: list[list[float]], b: list[float]) -> list[float]:
    """Solve A x = b by Gauss-Jordan with partial pivoting. lambda*I on the
    diagonal keeps A non-singular in practice."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        d = m[col][col] or _EPS
        m[col] = [v / d for v in m[col]]
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col]
                m[r] = [a - f * c for a, c in zip(m[r], m[col])]
    return [m[i][n] for i in range(n)]


def _sigmoid(z: float) -> float:
    if z >= 0:
        e = math.exp(-z)
        return 1.0 / (1.0 + e)
    e = math.exp(z)
    return e / (1.0 + e)


def _logloss(y: list[int], p: list[float]) -> float:
    return sum(
        -(yi * math.log(min(1 - _EPS, max(_EPS, pi))) + (1 - yi) * math.log(min(1 - _EPS, max(_EPS, 1 - pi))))
        for yi, pi in zip(y, p)
    ) / len(y)


@dataclass
class LearnResult:
    weights: dict          # every feature -> coefficient (pinned unchanged)
    bias: float
    n_pos: int
    n_neg: int
    cv_logloss: float
    cv_logloss_prior: float
    beats_prior: bool
    adopt: bool            # all gates clear
    blockers: list         # why adopt is False


class LinearModel:
    def __init__(self, model_cfg: dict, state: dict | None = None):
        sc = model_cfg["scoring"]
        if sc.get("stochastic"):
            raise NotImplementedError("scoring.stochastic is reserved for a future Thompson variant")

        # column-group features (manifest `expands: true`, e.g. reader_recs) have
        # no config weight — they expand to one weight per sub-id (reader_recs:<id>)
        # at learn time, prior 0.
        self.groups: set[str] = set()
        try:
            import tjvz.features as _F
            _F.load_all()
            self.groups = {
                f for f in sc["features"]
                if f in _F.REGISTRY and _F.REGISTRY[f].MANIFEST.get("expands")
            }
        except Exception:
            pass

        self.feat_ids: list[str] = [f for f in sc["features"] if f not in self.groups]
        self.weights: dict[str, float] = {f: float(sc["features"][f]["weight"]) for f in self.feat_ids}
        self.pinned = {f for f in self.feat_ids if sc["features"][f].get("pinned")}

        # per-sub-id priors / pins for a group feature (config.yaml
        # model.scoring.features.<group>.readers.<source> = {weight, pinned}).
        for g in self.groups:
            for sub, spec in (sc["features"][g].get("readers") or {}).items():
                col = f"{g}:{sub.strip().lower()}"
                self.weights[col] = float(spec.get("weight", 0.0))
                if spec.get("pinned"):
                    self.pinned.add(col)

        self.prior_weights = dict(self.weights)
        self.bias = float(sc.get("bias", 0.0))
        self.learning = model_cfg.get("learning")
        if state and state.get("source") == "learned":
            self.weights.update({k: float(v) for k, v in state["weights"].items()})
            self.bias = float(state["bias"])

    # --- scoring ---------------------------------------------------------------

    def score(self, feats: dict) -> float:
        return self.bias + sum(w * float(feats.get(k, 0.0)) for k, w in self.weights.items())

    def explain(self, feats: dict) -> dict:
        keys = list(self.weights) + [k for k in feats if k not in self.weights]
        terms = [
            {
                "feature": k,
                "value": round(float(feats.get(k, 0.0)), 4),
                "weight": round(self.weights.get(k, 0.0), 4),
                "contribution": round(self.weights.get(k, 0.0) * float(feats.get(k, 0.0)), 4),
            }
            for k in keys
        ]
        terms.sort(key=lambda t: -abs(t["contribution"]))
        return {"score": round(self.score(feats), 4), "bias": round(self.bias, 4), "terms": terms}

    # --- learning ------------------------------------------------------------

    def learn(self, rows: list[dict]) -> LearnResult:
        """rows: [{"decision": "read"|"promote"|"dismiss", "features": {id: value}}],
        features already recomputed leave-one-out / as-of the event."""
        if not self.learning:
            raise ValueError("config has no model.learning block")
        util = self.learning.get("utility", "log_odds")
        if util != "log_odds":
            raise NotImplementedError(f"utility {util!r} not implemented (v1: log_odds only)")

        pos = {_ALIAS.get(k, k) for k in self.learning.get("label", {}).get("positive", ["read", "stack"])}
        group_cols = sorted({
            k for r in rows for k in r["features"]
            if ":" in k and k.split(":", 1)[0] in self.groups and k not in self.pinned
        })
        free = [f for f in self.feat_ids if f not in self.pinned] + group_cols

        y = self._labels(rows, pos)
        n_pos = sum(y)
        n_neg = len(y) - n_pos

        w_free, bias = self._newton(rows, y, free)
        weights = dict(self.prior_weights)
        weights.update(dict(zip(free, w_free)))

        adopt = self.learning.get("adopt", {})
        k = adopt.get("cv_folds", 5)
        cv_learned = self._cv(rows, pos, free, k, prior=False)
        cv_prior = self._cv(rows, pos, free, k, prior=True)
        beats = cv_learned < cv_prior

        blockers: list[str] = []
        if n_pos < adopt.get("min_pos", 0):
            blockers.append(f"n_pos {n_pos} < {adopt['min_pos']}")
        if n_neg < adopt.get("min_neg", 0):
            blockers.append(f"n_neg {n_neg} < {adopt['min_neg']}")
        varying = sum(1 for f in free if len({r["features"].get(f, 0.0) for r in rows}) > 1)
        if varying < adopt.get("min_varying_features", 0):
            blockers.append(f"{varying} varying features < {adopt['min_varying_features']}")
        if not beats:
            blockers.append(f"cv log-loss {cv_learned:.4f} not < prior {cv_prior:.4f}")

        return LearnResult(weights, bias, n_pos, n_neg, cv_learned, cv_prior, beats, not blockers, blockers)

    # --- internals ---------------------------------------------------------------

    def _labels(self, rows, pos) -> list[int]:
        return [1 if _ALIAS.get(r["decision"], r["decision"]) in pos else 0 for r in rows]

    def _newton(self, rows, y, free, max_iter: int = 50) -> tuple[list[float], float]:
        n = len(y)
        d = len(free)
        prior = [self.prior_weights.get(f, 0.0) for f in free]
        pin = [(f, self.prior_weights[f]) for f in self.pinned]

        reg = self.learning.get("regularization") or {}
        lam0 = reg.get("lambda0", 1.0)
        n0 = reg.get("anneal_n0", 50)
        lam = max(lam0 * n0 / (n0 + n) if n else lam0, 1e-3)  # floor: never fully unregularised
        b0 = self.bias

        xf = [[float(r["features"].get(f, 0.0)) for f in free] for r in rows]
        off = [sum(w0 * float(r["features"].get(f, 0.0)) for f, w0 in pin) for r in rows]

        def penalised_loss(w, b) -> float:
            tot = 0.0
            for i in range(n):
                z = b + off[i] + sum(w[j] * xf[i][j] for j in range(d))
                p = _sigmoid(z)
                tot -= y[i] * math.log(max(_EPS, p)) + (1 - y[i]) * math.log(max(_EPS, 1 - p))
            tot /= max(n, 1)
            tot += 0.5 * lam * (sum((w[j] - prior[j]) ** 2 for j in range(d)) + (b - b0) ** 2)
            return tot

        w = list(prior)
        b = b0
        loss = penalised_loss(w, b)
        for _ in range(max_iter):
            p = [_sigmoid(b + off[i] + sum(w[j] * xf[i][j] for j in range(d))) for i in range(n)]
            resid = [p[i] - y[i] for i in range(n)]
            s = [pi * (1 - pi) for pi in p]

            g = [
                sum(resid[i] * xf[i][j] for i in range(n)) / n + lam * (w[j] - prior[j])
                for j in range(d)
            ]
            g.append(sum(resid) / n + lam * (b - b0))

            H = [[0.0] * (d + 1) for _ in range(d + 1)]
            for j in range(d):
                for k in range(j, d):
                    v = sum(s[i] * xf[i][j] * xf[i][k] for i in range(n)) / n
                    H[j][k] = H[k][j] = v + (lam if j == k else 0.0)
                hb = sum(s[i] * xf[i][j] for i in range(n)) / n
                H[j][d] = H[d][j] = hb
            H[d][d] = sum(s) / n + lam

            step = _solve(H, g)
            # damped Newton: back off until the penalised loss actually drops
            t = 1.0
            for _bt in range(20):
                nw = [w[j] - t * step[j] for j in range(d)]
                nb = b - t * step[d]
                nl = penalised_loss(nw, nb)
                if nl <= loss + 1e-12:
                    break
                t *= 0.5
            else:
                break  # no descent direction — separable / converged
            w, b, prev = nw, nb, loss
            loss = nl
            if max(abs(t * x) for x in step) < 1e-9 or prev - loss < 1e-12:
                break
        return w, b

    def _cv(self, rows, pos, free, k, prior: bool) -> float:
        k = min(k, len(rows)) if len(rows) >= 2 else 1
        if k < 2:
            return float("inf")
        folds = [rows[i::k] for i in range(k)]
        losses = []
        for i in range(k):
            test = folds[i]
            train = [r for j in range(k) if j != i for r in folds[j]]
            if not test or not train:
                continue
            if prior:
                w, b = dict(self.prior_weights), self.bias
            else:
                wf, b = self._newton(train, self._labels(train, pos), free)
                w = {**self.prior_weights, **dict(zip(free, wf))}
            yhat = [
                _sigmoid(b + sum(wv * float(r["features"].get(kk, 0.0)) for kk, wv in w.items()))
                for r in test
            ]
            losses.append(_logloss(self._labels(test, pos), yhat))
        return sum(losses) / len(losses) if losses else float("inf")
