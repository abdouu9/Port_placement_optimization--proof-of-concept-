from collections import Counter
import numpy as np
import pandas as pd
from datetime import datetime
import pprint
import math
from matplotlib import pyplot as plt
import copy, random

df_pile = pd.read_excel("yard_structure_generated.xlsx")
df_conteneur = pd.read_excel("containers_pool.xlsx", parse_dates=["date_arrivee", "date_depart"])

# Sélectionner les colonnes utiles
df_pile = df_pile[['pile_id', 'zone', 'zone_label', 'block', 'bay', 'row', 'x_coord',
       'y_coord', 'max_tier', 'capacity_teu', 'allowed_types',
       'allowed_shipping_lines', 'current_height', 'current_full_count',
       'top_weight_kg', 'top_container_id',
       'cooling_power', 'occupancy_rate', 'preassigned_voyage']]

df_conteneur = df_conteneur[['container_id', 'type', 'shipping_line', 'voyage_id', 'import_export',
       'size_ft', 'plein', 'poids_kg', 'date_arrivee', 'date_depart']]

# Transformer en liste de dictionnaires
df_pile = df_pile.to_dict('records')
conteneurs_list = df_conteneur.to_dict('records')


yard = {}

def _to_list(v):
    if v is None: return []
    try:
        if isinstance(v, float) and math.isnan(v): return []
    except: pass
    if isinstance(v, str):
        return [s.strip().upper() for s in v.split(';') if s.strip()]
    if isinstance(v, (list, tuple)):
        return [str(x).upper().strip() for x in v]
    return []

for pile in df_pile:

    block = pile['block']

    if block not in yard:
        yard[block] = []

    pile_data = {
        "pile_id": pile['pile_id'],
        "zone": pile['zone'],
        "zone_label": pile['zone_label'],
        "block": pile['block'],
        "coord": (pile['x_coord'], pile['y_coord']),
        "max_tier": pile['max_tier'],
        "capacity_teu": pile['capacity_teu'],
        "allowed_types": _to_list(pile.get('allowed_types')),
        "allowed_shipping_lines": _to_list(pile.get('allowed_shipping_lines')),
        "cooling_power": pile['cooling_power'],
        # état dynamique
        "nb_total_conteneurs": 0,
        "nb_pleins": 0,
        "poids_total": 0,
        "conteneurs": [],
        "current_height": 0,
        "current_full_count": 0,
        "top_weight_kg": 0,

    }

    yard[block].append(pile_data)



# ================================
# Paramètres de scoring
# ================================
ALPHA = -4.0    # occupancy
BETA  = 5.0    # shipping mismatch
GAMMA = 0.01   # weight (kg)
DELTA = 20.0   # dépassement de pleins
ZETA  = 50.0   # priorité départ (bonus)
MAX_FULLS_ALLOWED = 4
INF = 1e9
BIG = 1e3     # penalité regroupement sl


GROUP_BONUS = 600.0     # bonus (négatif) * same_count dans la pile -> augmente le score si regroupé
EMPTY_PENALTY = 1000.0    # malus si pile vide (éviter d'ouvrir de nouvelles piles)
PREASSIGNED_BONUS = 500.0  # si pile.preassigned_voyage == cont.voyage_id, grand bonus
SAME_KEY_BONUS = 120.0   # si clé (voyage,date) déjà présente, bonus supplémentaire
SAME_DEPART_BONUS = 60.0
GROUPING_BIG = 1000.0   # ancien BIG
GROUPING_CAP = 2000.0   # nouveau plafond
RTG_START = (0, 0)        # point de départ (ex: quai). Mets les coords qui font sens chez toi
RTG_SPEED = 1.5           # m/s (exemple).
LIFT_TIME = 20.0          # s (lever)
DROP_TIME = 20.0          # s (poser)

# pondération dans le score (tu peux préférer un coût en secondes directement)
EPS_DISTANCE = 0.1        # poids de la pénalité distance/temps dans le score


# ================================
# Fonctions
# ================================


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])

def _make_cont_key(cont):
    """Clé de groupe utilisée pour le future_count et la comparaison :
    (SHIPPING_LINE_UPPER, date_depart (pandas.Timestamp) ou None)
    """
    voy_id = (str(cont.get('voyage_id') or '')).upper().strip()
    dep = cont.get('date_depart')
    if pd.isna(dep):
        dep_key = None
    else:
        dep_key = pd.Timestamp(dep)
    return (voy_id, dep_key)

def _cont_depart(cont):
    dep = cont.get('date_depart')
    if pd.isna(dep):
        dep_key = None
    else:
        dep_key = pd.Timestamp(dep)
    return (dep_key)

def compute_score(cont, pile, now_dt=None, future_count=None, rtg_pos=None):
    if now_dt is None:
        now_dt = datetime.now()
    if rtg_pos is None:
        rtg_pos = RTG_START

    details = {"pile_id": pile["pile_id"], "container_id": cont["container_id"]}

    # contraintes dures
    if pile["allowed_types"]:
        if str(cont["type"]).upper() not in pile["allowed_types"]:
            return INF, {"reason": "type_not_allowed", **details}

    if pile["zone_label"]:
        if str(cont["import_export"]).upper() not in str(pile["zone_label"]).upper():
            return INF, {"reason": "zone_mismatch", **details}


    if str(cont["type"]).upper() == "REEFER" and not pile["cooling_power"]:
        return INF, {"reason": "reefer_no_power", **details}

    if pile["current_height"] >= pile["max_tier"]:
        return INF, {"reason": "height_exceeded", **details}

    # heures jusqu'au départ
    dep = cont.get("date_depart")
    if pd.isna(dep):
        cont_hours = 1e6
    else:
        cont_hours = max(0.0, (dep - now_dt).total_seconds() / 3600.0)

    # occupancy (favorise piles déjà remplies si ALPHA négatif)
    pen_occupancy = ALPHA * (pile["current_height"] / max(1, pile["max_tier"]))
    details["pen_occupancy"] = pen_occupancy

    # --- regroupement par voyage_id (forte préférence) ---
    cont_voy = (str(cont.get("voyage_id") or "")).upper().strip()
    pile_voy = (str(pile.get("preassigned_voyage") or "")).upper().strip() if pile.get("preassigned_voyage") else None
    same_count = sum(1 for c in pile.get("conteneurs", []) if str((c.get("voyage_id") or "")).upper().strip() == cont_voy)

    pen_voyage = 0.0
    if pile_voy and pile_voy != "":
        # si la pile a preassigned_voyage et diffère -> rejet strict (on veut garder cohérence)
        if pile_voy != cont_voy:
            return INF, {"reason": "voyage_mismatch", **details}
        else:
            pen_voyage -= PREASSIGNED_BONUS

    # bonus qui dépend du nombre déjà présents (favorise empilement progressif)
    if same_count > 0:
        pen_voyage -= GROUP_BONUS * same_count
        pen_voyage -= 50.0 * pile["current_height"]

    # petit malus si pile vide pour éviter d'ouvrir trop de piles
    if not pile.get("conteneurs"):
        pen_voyage += EMPTY_PENALTY

    details["pen_voyage"] = pen_voyage
    details["same_count_in_pile"] = same_count

    # shipping / group keys
    existing_keys = {_make_cont_key(c) for c in pile["conteneurs"] if c}
    existing_cont_depart = {_cont_depart(c) for c in pile["conteneurs"] if c}

    if _make_cont_key(cont) in existing_keys:
        pen_shipping = -SAME_KEY_BONUS
    else:
        pen_shipping = BETA
    details["pen_shipping"] = pen_shipping

    if _cont_depart(cont) in existing_cont_depart:
        pen_same_depart = -SAME_DEPART_BONUS
    else:
        pen_same_depart = BETA
    details["pen_same_depart"] = pen_same_depart

    # allowed_shipping_lines penalty (soft)
    cont_sl = (str(cont.get("shipping_line") or "")).upper().strip()
    if pile.get("allowed_shipping_lines"):
        allowed_list = [str(s).upper().strip() for s in pile["allowed_shipping_lines"] if s]
        if cont_sl not in allowed_list:
            pen_shipping += 10.0
            details["pen_shipping"] = pen_shipping

    # grouping_penalty (réserver espace pour les futurs de même groupe)
    grouping_penalty = 0.0
    grouping_info = None
    if future_count is not None and len(pile['conteneurs']) > 0:
        pile_groups = Counter(_make_cont_key(c) for c in pile['conteneurs'] if c)
        if pile_groups:
            pile_group_key, pile_group_count = pile_groups.most_common(1)[0]
            future_same_for_pile = future_count.get(pile_group_key, 0)
            free_slots_now = pile['max_tier'] - pile['current_height']
            cont_key = _make_cont_key(cont)
            # si on met un cont différent on consomme 1 slot en moins pour le groupe majeur
            if cont_key != pile_group_key:
                remaining_free_after = free_slots_now - 1
            else:
                remaining_free_after = free_slots_now
            needed_after = max(0, future_same_for_pile - remaining_free_after)
            if needed_after > 0:
                grouping_penalty = min(needed_after * (GROUPING_BIG / 10.0), GROUPING_CAP)
                grouping_info = {"reason": "grouping_penalty_blocked", "needed_after": needed_after, **details}

    details["grouping_penalty"] = grouping_penalty
    if grouping_info is not None:
        details["grouping_info"] = grouping_info

    # poids (heavy-on-light)
    top_w = pile.get("top_weight_kg") or 0.0
    poids_diff = float(cont.get("poids_kg") or 0.0) - float(top_w)
    if pile["top_weight_kg"] > 0:
        MAX_DIFF_HARD = 8000
        if poids_diff >= MAX_DIFF_HARD:
            return INF, {"reason": f"heavy_on_light (diff={poids_diff})", **details}
        if poids_diff > 0:
            pen_weight = GAMMA * (poids_diff / 1000.0)
        else:
            pen_weight = GAMMA * (poids_diff / 2500.0)
    else:
        pen_weight = -3.0
    details["pen_weight"] = pen_weight

    # pleins
    next_fulls = pile.get("current_full_count", 0) + int(cont.get("plein", 0))
    pen_fulls = DELTA * max(0, next_fulls - MAX_FULLS_ALLOWED)
    details["pen_fulls"] = pen_fulls

    # priorité date de départ: si cont est plus tard que top -> bloquer (pour ne pas bloquer)
    top_cont_hours = pile.get("hours_to_depart_top")
    if top_cont_hours is not None and cont_hours > float(top_cont_hours) + 1e-6:
        return INF, {"reason": "blocked_by_depart", **details}

    # distance
    dist = manhattan(rtg_pos, pile.get('coord'))
    travel_time_s = dist / max(RTG_SPEED, 1e-6)
    handling_time_s = LIFT_TIME + DROP_TIME
    pen_distance = EPS_DISTANCE * (travel_time_s + handling_time_s)
    details["pen_distance"] = pen_distance

    # score final
    score = (pen_occupancy + pen_shipping + pen_weight + pen_fulls +
             grouping_penalty + pen_distance + pen_same_depart + pen_voyage)
    details["final_score"] = score

    return score, details

def _tie_better(sc_new, sc_best, details_new, pile_new, details_best, pile_best,rtg_pos=None):
    if rtg_pos is None:
        rtg_pos = RTG_START
    # considère comme "meilleur" si strictly better
    s_new = details_new.get('same_count_in_pile', 0)
    s_best = details_best.get('same_count_in_pile', 0)
    if s_new != s_best:
        return s_new > s_best
    h_new = pile_new.get('current_height', 0)
    h_best = pile_best.get('current_height', 0)
    if h_new != h_best:
        return h_new > h_best
    # distance: plus petit mieux
    d_new = abs(rtg_pos[0] - pile_new['coord'][0]) + abs(rtg_pos[1] - pile_new['coord'][1])
    d_best = abs(rtg_pos[0] - pile_best['coord'][0]) + abs(rtg_pos[1] - pile_best['coord'][1])
    return d_new < d_best


def placer(conteneurs_list, yard, now_dt=None, debug=True):

    conteneurs_list.sort(
        key=lambda c: (
            c.get('voyage_id'),
            c.get('date_depart') or pd.Timestamp.max
        )
    )

    placements = []
    if now_dt is None:
        now_dt = datetime.now()

    future_count = Counter(_make_cont_key(c) for c in conteneurs_list)
    rtg_pos = RTG_START
    total_distance = 0.0
    total_time = 0.0

    for idx, cont in enumerate(conteneurs_list):
        best_pile = None
        best_score = INF
        best_cont_hours = None
        cont_sl = (str(cont.get("shipping_line") or "")).upper().strip()

        if debug:
            print(f"\n[CONTAINER {cont['container_id']}] voyage={cont.get('voyage_id')} "
                  f"type={cont.get('type')} poids={cont.get('poids_kg')}")

        for _, piles in yard.items():
            for pile in piles:
                sc, details = compute_score(cont, pile, now_dt=now_dt,
                                           future_count=future_count, rtg_pos=rtg_pos)
                if debug :
                    if sc >= INF:
                        print(f"  Pile {pile['pile_id']} rejetée : {details.get('reason')}")
                    else:
                        print(f"  Pile {pile['pile_id']} score={sc}")
                        for k, v in details.items():
                            if k not in ["pile_id", "container_id"]:
                                print(f"    - {k}: {v}")

                if sc < best_score:
                    best_score = sc
                    best_pile = pile
                    best_details = details
                    dep = cont.get("date_depart")
                    best_cont_hours = 1e6 if pd.isna(dep) else max(0.0, (dep - now_dt).total_seconds() / 3600.0)

                elif abs(sc - best_score) <= 1e-9 and best_pile is not None:
                    if _tie_better(sc, best_score, details, pile, best_details, best_pile,rtg_pos=rtg_pos):
                        best_score = sc
                        best_pile = pile
                        best_details = details
                        dep = cont.get("date_depart")
                        best_cont_hours = 1e6 if pd.isna(dep) else max(0.0, (dep - now_dt).total_seconds() / 3600.0)

        if best_pile is None or best_score >= INF:
            placements.append({
                "container_id": cont["container_id"],
                "chosen_pile_id": None,
                "score": None,
                "reason": "no_candidate"
            })
            if debug :
                print(f"==> Aucun emplacement trouvé pour {cont['container_id']}")
            continue

        # mise à jour de la pile choisie
        if len(best_pile["conteneurs"]) == 0:
            best_pile["allowed_shipping_lines"] = [cont_sl] if cont_sl else []
        best_pile["conteneurs"].append(cont)
        best_pile["nb_total_conteneurs"] += 1
        if int(cont.get("plein", 0)) == 1:
            best_pile["nb_pleins"] += 1
            best_pile["current_full_count"] += 1

        best_pile["poids_total"] += float(cont.get("poids_kg", 0.0))
        best_pile["current_height"] += 1
        best_pile["top_weight_kg"] = float(cont.get("poids_kg", 0.0))
        best_pile["hours_to_depart_top"] = best_cont_hours
        best_pile["occupancy_rate"] = best_pile["current_height"] / max(1, best_pile["max_tier"])
        if not best_pile.get("preassigned_voyage"):
            best_pile["preassigned_voyage"] = cont.get("voyage_id")
        cont['stack_level'] = best_pile["current_height"]

        dist = manhattan(rtg_pos, best_pile["coord"])
        travel_time_s = dist / max(RTG_SPEED, 1e-6)
        handling_time_s = LIFT_TIME + DROP_TIME
        # cumul
        total_distance += dist
        total_time += travel_time_s + handling_time_s

        rtg_pos = best_pile["coord"]

        cont_key = _make_cont_key(cont)
        if future_count.get(cont_key, 0) > 0:
            future_count[cont_key] -= 1
            if future_count[cont_key] == 0:
                del future_count[cont_key]

        placements.append({
            "container_id": cont["container_id"],
            "chosen_pile_id": best_pile["pile_id"],
            "score": best_score,
            "reason": "placed"
        })

        if debug :
            print(f"==> Choix final: pile {best_pile['pile_id']} avec score {best_score}")

    return placements, total_distance, total_time

placements = []
placements,dist_total,temps_total=placer(conteneurs_list, yard, now_dt=None,debug=False)





def _to_timestamp(v):
    if v is None: return None
    try:
        if pd.isna(v): return None
    except:
        pass
    return pd.Timestamp(v)



def validate_yard(yard):
    violations = []
    for block, piles in yard.items():
        for pile in piles:
            pid = pile['pile_id']
            # hauteur physique
            if pile['current_height'] > pile['max_tier']:
                violations.append((pid, 'HEIGHT_EXCEEDED', pile['current_height'], pile['max_tier']))
            # pleins
            if pile['current_full_count'] > MAX_FULLS_ALLOWED:
                violations.append((pid, 'TOO_MANY_FULLS', pile['current_full_count'], MAX_FULLS_ALLOWED))
            # type / reefer / cooling
            for idx, cont in enumerate(pile['conteneurs']):
                cont_id = cont.get('container_id')
                ctype = str(cont.get('type') or '').upper()
                if pile.get('allowed_types'):
                    if ctype not in pile['allowed_types']:
                        violations.append((pid, 'TYPE_MISMATCH', cont_id, ctype, pile['allowed_types']))
                if ctype == 'REEFER' and not pile.get('cooling_power'):
                    violations.append((pid, 'REEFER_NO_POWER', cont_id))
            # heavy-on-light (adjacent check)
            for i in range(len(pile['conteneurs']) - 1):
                below = pile['conteneurs'][i]
                above = pile['conteneurs'][i+1]
                w_b = float(below.get('poids_kg') or 0.0)
                w_a = float(above.get('poids_kg') or 0.0)
                if w_a > w_b +8000:
                    violations.append((pid, 'HEAVY_ON_LIGHT', above.get('container_id'), w_a, below.get('container_id'), w_b))
            # blocking by date: any cont that departs earlier than someone above
            for i, cont in enumerate(pile['conteneurs']):
                cont_dep = _to_timestamp(cont.get('date_depart'))
                if cont_dep is None:
                    continue
                # any above has later date? if yes, cont is blocked
                for j in range(i+1, len(pile['conteneurs'])):
                    above = pile['conteneurs'][j]
                    above_dep = _to_timestamp(above.get('date_depart'))
                    if above_dep is None:
                        continue
                    # cont_dep earlier (smaller) than above_dep => blocked
                    if cont_dep < above_dep:
                        violations.append((pid, 'BLOCKED_BY_ABOVE', cont.get('container_id'), cont_dep, above.get('container_id'), above_dep))
                        break
    return violations


def estimate_rehandles(yard, now_dt=None, return_back=True):

    if now_dt is None:
        now_dt = datetime.now()

    total_rehandle_ops = 0
    total_containers_moved = 0
    per_pile = []

    for block, piles in yard.items():
        for pile in piles:
            stack = [dict(c) for c in pile.get('conteneurs', [])]

            # convertir date_depart -> _dep_hours (heures jusqu'au départ)
            for c in stack:
                dep = c.get('date_depart')
                dep_dt = None
                if dep is None:
                    dep_dt = None
                else:
                    try:
                        if pd is not None and pd.isna(dep):
                            dep_dt = None
                        elif hasattr(dep, "to_pydatetime"):
                            dep_dt = dep.to_pydatetime()
                        elif isinstance(dep, datetime):
                            dep_dt = dep
                        else:
                            if pd is not None:
                                tmp = pd.to_datetime(dep, errors="coerce")
                                if pd.isna(tmp):
                                    dep_dt = None
                                else:
                                    dep_dt = tmp.to_pydatetime()
                            else:
                                dep_dt = None
                    except Exception:
                        dep_dt = None

                if dep_dt is None:
                    c['_dep_hours'] = None
                else:
                    c['_dep_hours'] = (dep_dt - now_dt).total_seconds() / 3600.0

            pile_ops = 0
            pile_moved = 0
            events = []

            while True:
                indices = [i for i, c in enumerate(stack) if c.get('_dep_hours') is not None]
                if not indices:
                    break

                # choisir la cible la plus proche (heures les plus petites).
                # si égalité, choisir l'index le plus grand (contourner la tie en faveur du haut)
                target_idx = min(indices, key=lambda i: (stack[i]['_dep_hours'], -i))
                target = stack[target_idx]
                above_count = len(stack) - 1 - target_idx

                if above_count == 0:
                    events.append({
                        'action': 'pop_no_rehandle',
                        'target_id': target.get('container_id'),
                        'target_dep': target.get('date_depart'),
                        'above_count': 0
                    })
                    stack.pop(target_idx)
                    continue

                # calculer popped/moved_ids AVANT de modifier stack (fix du bug)
                popped = stack[target_idx + 1:]
                moved_ids = [c.get('container_id') for c in popped]
                moved_deps = [c.get('date_depart') for c in popped]
                moved_hours = [(c['_dep_hours'] if c.get('_dep_hours') is not None else float('inf')) for c in popped]
                target_hour = target['_dep_hours']

                # déplacer si tous les conteneurs au-dessus partent plus tard que la cible
                if all(ph > target_hour for ph in moved_hours):
                    note = None
                else:
                    # cas théorique improbable (cohérence de données) -> on rehandle quand même et on note
                    note = "some_above_have_earlier_or_equal_dep; rehandled anyway"

                pile_ops += 1
                pile_moved += above_count
                total_rehandle_ops += 1
                total_containers_moved += above_count

                events.append({
                    'action': 'rehandle',
                    'target_id': target.get('container_id'),
                    'target_dep': target.get('date_depart'),
                    'moved_count': above_count,
                    'moved_ids': moved_ids,
                    'moved_deps': moved_deps,
                    'moved_hours': moved_hours,
                    'note': note
                })

                below = stack[:target_idx]
                stack = below
                if return_back:
                    stack.extend(popped)

            per_pile.append({
                'pile_id': pile.get('pile_id'),
                'ops': pile_ops,
                'containers_moved': pile_moved,
                'events': events,
                'final_stack_size': len(stack)
            })

    return {
        'total_rehandle_ops': total_rehandle_ops,
        'total_containers_moved': total_containers_moved,
        'per_pile': per_pile

    }


def grouping_metrics(yard):
    pile_stats = []
    for block, piles in yard.items():
        for pile in piles:
            total = len(pile['conteneurs'])
            if total == 0:
                pile_stats.append({'pile_id': pile['pile_id'], 'total':0, 'top_group_frac': None})
                continue
            # group by (shipping_line, date_depart)
            groups = Counter()
            for c in pile['conteneurs']:
                key = (str(c.get('voayage_id') or '').upper().strip(), _to_timestamp(c.get('date_depart')))
                groups[key] += 1
            top_group_count = groups.most_common(1)[0][1]
            pile_stats.append({
                'pile_id': pile['pile_id'],
                'total': total,
                'top_group_count': top_group_count,
                'top_group_frac': top_group_count / total
            })
    # metrics
    avg_purity = sum(p.get('top_group_frac') or 0.0 for p in pile_stats) / max(1, len(pile_stats))
    empty_pct = sum(1 for p in pile_stats if p['total']==0) / max(1, len(pile_stats))
    return {'pile_stats': pile_stats, 'avg_purity': avg_purity, 'empty_pct': empty_pct}




# 5) Fonction qui appelle tout et produit un rapport lisible
def analyze_yard(yard):
    viol = validate_yard(yard)
    rehandles = estimate_rehandles(yard)
    grouping = grouping_metrics(yard)
    report = {
        'violations_count': len(viol),
        'violations': viol[:50],   # montre max 50 items
        'estimated_rehandles': rehandles,
        'grouping_avg_purity': grouping['avg_purity'],
        'grouping_empty_pct': grouping['empty_pct']
    }
    return report







def placer_random(conteneurs_list, yard):
    rtg_pos = RTG_START
    total_distance = 0.0
    total_time = 0.0
    placements = []

    for cont in conteneurs_list:
        # choisir une pile au hasard qui n'est pas pleine
        candidates = [p for _, piles in yard.items() for p in piles if p["current_height"] < p["max_tier"]]
        if not candidates:
            continue
        pile = random.choice(candidates)

        dist = manhattan(rtg_pos, pile["coord"])
        travel_time_s = dist / max(RTG_SPEED, 1e-6)
        handling_time_s = LIFT_TIME + DROP_TIME

        total_distance += dist
        total_time += travel_time_s + handling_time_s

        rtg_pos = pile["coord"]
        pile["conteneurs"].append(cont)
        pile["current_height"] += 1

        placements.append({
            "container_id": cont["container_id"],
            "chosen_pile_id": pile["pile_id"],
            "reason": "random"
        })

    return placements, total_distance, total_time


# --- Yard optimisé ---
yard_opt = copy.deepcopy(yard)
for block, piles in yard_opt.items():
    for pile in piles:
        pile['conteneurs'] = []
        pile['current_height'] = 0

placements_opt, dist_opt, time_opt = placer(conteneurs_list, yard_opt, debug=False)

# --- Yard aléatoire ---
yard_rand = copy.deepcopy(yard)
for block, piles in yard_rand.items():
    for pile in piles:
        pile['conteneurs'] = []
        pile['current_height'] = 0

placements_rand, dist_rand, time_rand = placer_random(conteneurs_list, yard_rand)

# --- Pureté ---
grouping_opt = grouping_metrics(yard_opt)
grouping_rand = grouping_metrics(yard_rand)

pile_purity_opt = [p['top_group_frac'] or 0 for p in grouping_opt['pile_stats'] if p['total'] > 1]
pile_purity_rand = [p['top_group_frac'] or 0 for p in grouping_rand['pile_stats'] if p['total'] > 1]

plt.figure(figsize=(6,4))
plt.hist(pile_purity_opt, bins=np.linspace(0,1,11), alpha=0.5, label="Optimisé", edgecolor='black')
plt.hist(pile_purity_rand, bins=np.linspace(0,1,11), alpha=0.5, label="Aléatoire", edgecolor='black')
plt.xlabel("Proportion du groupe majoritaire dans une pile")
plt.ylabel("Nombre de piles")
plt.title("Comparaison pureté regroupements")
plt.legend()
plt.tight_layout()
#plt.show()

# --- Distances & temps ---
labels = ["Optimisé", "Aléatoire"]
distances = [dist_opt, dist_rand]
times = [time_opt, time_rand]

fig, ax = plt.subplots(1,2, figsize=(10,4))
ax[0].bar(labels, distances, color=['green','red'])
ax[0].set_ylabel("Distance totale")
ax[0].set_title("Distance parcourue RTG")
ax[1].bar(labels, times, color=['green','red'])
ax[1].set_ylabel("Temps total (s)")
ax[1].set_title("Temps de manutention")
plt.tight_layout()
#plt.show()


# Yard optimisé
yard_opt = copy.deepcopy(yard)
for block, piles in yard_opt.items():
    for pile in piles:
        pile['conteneurs'] = []
        pile['current_height'] = 0

placements_opt, dist_opt, time_opt = placer(conteneurs_list, yard_opt, debug=False)

report_opt = analyze_yard(yard_opt)
rehandles_opt = report_opt['estimated_rehandles']['total_rehandle_ops']


# Yard aléatoire
yard_rand = copy.deepcopy(yard)
for block, piles in yard_rand.items():
    for pile in piles:
        pile['conteneurs'] = []
        pile['current_height'] = 0

placements_rand, dist_rand, time_rand = placer_random(conteneurs_list, yard_rand)

report_rand = analyze_yard(yard_rand)
rehandles_rand = report_rand['estimated_rehandles']['total_rehandle_ops']

labels = ["Optimisé", "Aléatoire"]
distances = [dist_opt, dist_rand]
times = [time_opt, time_rand]
rehandles = [rehandles_opt, rehandles_rand]

x = np.arange(len(labels))
width = 0.25

fig, ax = plt.subplots(1,3, figsize=(12,4))

# distances
ax[0].bar(labels, distances, color=['green','red'])
ax[0].set_ylabel("Distance totale")
ax[0].set_title("Distance parcourue RTG")

# temps
ax[1].bar(labels, times, color=['green','red'])
ax[1].set_ylabel("Temps total (s)")
ax[1].set_title("Temps de manutention")

# rehandles
ax[2].bar(labels, rehandles, color=['green','red'])
ax[2].set_ylabel("Nombre de rehandles")
ax[2].set_title("Comparaison des rehandles")

plt.tight_layout()
#plt.show()

# === Violations pour placement aléatoire ===
violations_rand = validate_yard(yard_rand)

# Transformer en DataFrame pour compter les types
df_viol = pd.DataFrame(violations_rand, columns=["pile_id", "violation_type", "details1", "details2", "details3", "details4"])
viol_counts = df_viol["violation_type"].value_counts()

# Graphe
plt.figure(figsize=(8,5))
viol_counts.plot(kind="bar", color="orange", edgecolor="black")
plt.title("Violations de placement (Aléatoire)")
plt.xlabel("Type de violation")
plt.ylabel("Nombre de violations")
plt.xticks(rotation=45, ha="right")
plt.tight_layout()
plt.show()


report=analyze_yard(yard)
#print(report)
#pprint.pprint(yard)

print(yard.items())













































