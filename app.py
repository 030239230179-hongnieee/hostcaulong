"""Quản lý sân cầu lông vãng lai.

Chạy:  pip install streamlit
       streamlit run badminton_app.py

Dữ liệu tự lưu vào badminton_state.json cạnh file này, tải lại trang không mất.
"""

import itertools
import json
import os
import uuid
from datetime import datetime

import pandas as pd
import streamlit as st

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "badminton_state.json")
MAX_PLAYERS = 16
MAX_COURTS = 6
LEVELS = {"Mạnh": 3, "Bình thường": 2, "Yếu": 1}
GENDERS = ["Nam", "Nữ"]

# Trọng số của thuật toán gợi ý (chi phí càng thấp càng tốt)
POOL_SIZE = MAX_PLAYERS  # xét toàn bộ hàng chờ (tối đa 16 người)
W_BALANCE = 10      # mỗi 1 điểm chênh lệch sức mạnh giữa 2 đội
W_SPREAD = 3        # mỗi bậc chênh trình giữa người mạnh nhất và yếu nhất trong 4 người
W_PARTNER = 4       # mỗi lần 2 người đã đánh chung đội
W_OPP = 2           # mỗi lần 2 người đã đánh đối đầu
W_GAMES = 6         # mỗi trận mà một người đã đánh nhiều hơn người ít trận nhất
W_QUEUE = 1         # thứ tự trong hàng chờ

st.set_page_config(page_title="Quản lý sân cầu lông", layout="centered")


# ---------------------------------------------------------------- dữ liệu
def new_state():
    return {
        "players": {},
        "counter": 0,
        "courts": {"1": None, "2": None},
        "partners": {},
        "opponents": {},
        "history": [],
    }


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return new_state()


def save():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data(), f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def data():
    return st.session_state["S"]


if "S" not in st.session_state:
    st.session_state["S"] = load_state()
    st.session_state["sug_idx"] = 0

for _p in st.session_state["S"]["players"].values():
    _p.setdefault("gender", "Nam")  # dữ liệu cũ chưa có giới tính


def flash(kind, msg):
    st.session_state["flash"] = (kind, msg)


def next_counter():
    data()["counter"] += 1
    return data()["counter"]


def pkey(a, b):
    return "|".join(sorted((a, b)))


def lvl(pid):
    return LEVELS[data()["players"][pid]["level"]]


def team_txt(ids):
    P = data()["players"]
    return " + ".join(f"{P[i]['name']} ({P[i]['gender']}, {P[i]['level']})" for i in ids)


def playing_ids():
    ids = set()
    for m in data()["courts"].values():
        if m:
            ids.update(m["a"] + m["b"])
    return ids


def waiting_ids():
    """Người đang chờ, xếp theo ưu tiên: ít trận trước, cùng số trận thì chờ lâu hơn trước."""
    P = data()["players"]
    busy = playing_ids()
    ids = [i for i, p in P.items() if not p["rest"] and i not in busy]
    return sorted(ids, key=lambda i: (P[i]["games"], P[i]["queue_no"]))


def clear_keys(prefixes):
    for k in list(st.session_state.keys()):
        if any(k.startswith(p) for p in prefixes):
            del st.session_state[k]


# ---------------------------------------------------------------- thuật toán xếp trận
def eval_split(a, b):
    d = data()
    diff = abs(sum(lvl(i) for i in a) - sum(lvl(i) for i in b))
    partner = d["partners"].get(pkey(*a), 0) + d["partners"].get(pkey(*b), 0)
    opp = sum(d["opponents"].get(pkey(x, y), 0) for x in a for y in b)
    return {
        "a": a, "b": b, "diff": diff, "partner": partner, "opp": opp,
        "cost": diff * W_BALANCE + partner * W_PARTNER + opp * W_OPP,
    }


def is_female(pid):
    return data()["players"][pid]["gender"] == "Nữ"


def match_type(ids):
    """Loại trận theo giới tính của 4 người, None nếu không hợp lệ."""
    women = sum(is_female(i) for i in ids)
    return {0: "Đôi nam", 4: "Đôi nữ", 2: "Đôi nam nữ"}.get(women)


def ranked_splits(ids):
    """Các cách chia 4 người thành 2 cặp hợp lệ, tốt nhất đứng đầu.

    Đôi nam và đôi nữ: cả 3 cách chia đều được.
    Đôi nam nữ: mỗi cặp bắt buộc 1 nam 1 nữ (không được nam+nam đấu nữ+nữ).
    Bộ 3 nam 1 nữ hoặc 1 nam 3 nữ không xếp được, trả về danh sách rỗng.
    """
    kind = match_type(ids)
    if kind is None:
        return []
    a, b, c, e = ids
    options = []
    for x, y in (([a, b], [c, e]), ([a, c], [b, e]), ([a, e], [b, c])):
        if kind == "Đôi nam nữ" and sum(is_female(i) for i in x) != 1:
            continue
        s = eval_split(x, y)
        s["type"] = kind
        options.append(s)
    return sorted(options, key=lambda s: s["cost"])


def suggestions(top=8):
    """Duyệt mọi tổ hợp 4 người trong nhóm ưu tiên, mỗi tổ hợp lấy cách chia cặp tốt nhất."""
    P = data()["players"]
    wait = waiting_ids()
    if len(wait) < 4:
        return []
    min_games = min(P[i]["games"] for i in wait)
    pool = wait[:POOL_SIZE]
    results = []
    for combo in itertools.combinations(range(len(pool)), 4):
        ids = [pool[i] for i in combo]
        fair = sum((P[i]["games"] - min_games) * W_GAMES for i in ids) + sum(combo) * W_QUEUE
        levels = [lvl(i) for i in ids]
        spread = (max(levels) - min(levels)) * W_SPREAD
        splits = ranked_splits(ids)
        if not splits:
            continue
        best = splits[0]
        best["total"] = fair + spread + best["cost"]
        best["ids"] = ids
        results.append(best)
    results.sort(key=lambda s: s["total"])
    return results[:top]


# ---------------------------------------------------------------- hành động
def start_match(court, a, b):
    data()["courts"][court] = {"a": a, "b": b}
    clear_keys(["pick_"])
    st.session_state["sug_idx"] = 0
    save()


def finish_match(court):
    d = data()
    m = d["courts"].get(court)
    if not m:
        return
    a, b = m["a"], m["b"]
    for pid in a + b:
        p = d["players"].get(pid)
        if p:
            p["games"] += 1
            p["queue_no"] = next_counter()
    for team in (a, b):
        k = pkey(*team)
        d["partners"][k] = d["partners"].get(k, 0) + 1
    for x in a:
        for y in b:
            k = pkey(x, y)
            d["opponents"][k] = d["opponents"].get(k, 0) + 1
    names = lambda ids: [d["players"][i]["name"] for i in ids if i in d["players"]]
    d["history"].append({
        "time": datetime.now().strftime("%H:%M"),
        "court": court, "a": names(a), "b": names(b), "type": match_type(a + b),
    })
    d["courts"][court] = None
    st.session_state["sug_idx"] = 0
    save()


def cancel_match(court):
    data()["courts"][court] = None
    st.session_state["sug_idx"] = 0
    save()


def next_suggestion():
    st.session_state["sug_idx"] += 1


def add_player():
    d = data()
    name = st.session_state.get("new_name", "").strip()
    level = st.session_state.get("new_level", "Bình thường")
    gender = st.session_state.get("new_gender", "Nam")
    if not name:
        return flash("error", "Chưa nhập tên.")
    if len(d["players"]) >= MAX_PLAYERS:
        return flash("error", f"Đã đủ {MAX_PLAYERS} người.")
    if any(p["name"].lower() == name.lower() for p in d["players"].values()):
        return flash("error", f"Tên '{name}' đã có trong danh sách.")
    d["players"][uuid.uuid4().hex[:8]] = {
        "name": name, "gender": gender, "level": level, "games": 0, "rest": False,
        "queue_no": next_counter(),
    }
    save()


def set_level(pid):
    p = data()["players"][pid]
    p["level"] = st.session_state[f"lv_{pid}"]
    # Gợi ý và các cách chia cặp được tính lại từ đầu theo trình mới
    st.session_state["sug_idx"] = 0
    clear_keys(["split_"])
    save()
    flash("success", f"Đã đổi trình của {p['name']} thành {p['level']}. "
                     "Gợi ý xếp sân đã tính lại theo trình mới.")


def set_gender(pid):
    p = data()["players"][pid]
    if pid in playing_ids():
        st.session_state[f"gd_{pid}"] = p["gender"]
        return flash("error", f"{p['name']} đang đánh, kết thúc trận rồi mới đổi giới tính.")
    p["gender"] = st.session_state[f"gd_{pid}"]
    st.session_state["sug_idx"] = 0
    clear_keys(["split_"])
    save()


def set_rest(pid):
    p = data()["players"][pid]
    want = st.session_state[f"rest_{pid}"]
    if want and pid in playing_ids():
        st.session_state[f"rest_{pid}"] = False
        return flash("error", f"{p['name']} đang đánh, kết thúc trận rồi mới cho nghỉ.")
    p["rest"] = want
    if not want:
        p["queue_no"] = next_counter()
    save()


def delete_player(pid):
    d = data()
    if pid in playing_ids():
        return flash("error", f"{d['players'][pid]['name']} đang đánh, chưa xóa được.")
    d["players"].pop(pid, None)
    clear_keys([f"lv_{pid}", f"gd_{pid}", f"rest_{pid}", f"pick_{pid}"])
    save()


def set_courts():
    d = data()
    n = st.session_state["n_courts"]
    for i in range(1, n + 1):
        d["courts"].setdefault(str(i), None)
    for k in list(d["courts"]):
        if int(k) > n and d["courts"][k] is None:
            del d["courts"][k]
    save()


def reset_stats():
    d = data()
    for p in d["players"].values():
        p.update(games=0, rest=False, queue_no=0)
    d["courts"] = {k: None for k in d["courts"]}
    d.update(counter=0, partners={}, opponents={}, history=[])
    clear_keys(["pick_", "rest_", "split_"])
    st.session_state["sug_idx"] = 0
    save()


def reset_all():
    st.session_state["S"] = new_state()
    clear_keys(["pick_", "rest_", "lv_", "gd_", "split_", "n_courts"])
    st.session_state["sug_idx"] = 0
    save()


# ---------------------------------------------------------------- giao diện
st.title("Sân cầu lông vãng lai")

if "flash" in st.session_state:
    kind, msg = st.session_state.pop("flash")
    (st.error if kind == "error" else st.success)(msg)

tab_court, tab_players, tab_stats = st.tabs(["Điều phối sân", "Người chơi", "Thống kê"])
d = data()
P = d["players"]

# ---- Tab 1: điều phối sân
with tab_court:
    court_keys = sorted(d["courts"], key=int)
    for k in court_keys:
        m = d["courts"][k]
        with st.container(border=True):
            if m:
                st.markdown(f"**Sân {k}** đang đánh · {match_type(m['a'] + m['b']) or ''}")
                st.write(f"{team_txt(m['a'])}  \nvs  \n{team_txt(m['b'])}")
                gap = eval_split(m["a"], m["b"])["diff"]
                if gap >= 2:
                    st.warning(f"Hai đội đang lệch trình ({gap}). Nếu vừa đổi trình, "
                               "bấm Hủy để xếp lại trận này.")
                c1, c2 = st.columns([3, 1])
                c1.button("Kết thúc trận", key=f"fin_{k}", type="primary",
                          on_click=finish_match, args=(k,), use_container_width=True)
                c2.button("Hủy", key=f"can_{k}", on_click=cancel_match, args=(k,),
                          help="Trả người về hàng chờ, không tính trận",
                          use_container_width=True)
            else:
                st.markdown(f"**Sân {k}** trống")

    wait = waiting_ids()
    if wait:
        line = " · ".join(f"{P[i]['name']} ({P[i]['gender']}, {P[i]['level']}, {P[i]['games']} trận)" for i in wait)
        st.markdown(f"**Đang chờ ({len(wait)})**, xếp theo thứ tự ưu tiên")
        st.write(line)
    else:
        st.markdown("**Đang chờ (0)**")

    st.divider()
    mode = st.radio("Cách điều phối", ["Tự chọn người", "Gợi ý tự động"], horizontal=True)
    free = [k for k in court_keys if d["courts"][k] is None]
    sugs = suggestions() if (mode == "Gợi ý tự động" and free and len(wait) >= 4) else []

    if not free:
        st.info("Chưa có sân trống. Kết thúc một trận để xếp tiếp.")
    elif len(wait) < 4:
        st.info("Cần ít nhất 4 người đang chờ.")

    elif mode == "Gợi ý tự động" and not sugs:
        st.info("Chưa ghép được trận hợp lệ. Hàng chờ cần đủ 4 nam, 4 nữ hoặc 2 nam + 2 nữ.")

    elif mode == "Tự chọn người":
        court = st.selectbox("Vào sân", free, format_func=lambda k: f"Sân {k}", key="manual_court")
        cols = st.columns(2)
        for n, pid in enumerate(wait):
            p = P[pid]
            cols[n % 2].checkbox(f"{p['name']} · {p['gender']} · {p['level']} · {p['games']} trận", key=f"pick_{pid}")
        chosen = [pid for pid in wait if st.session_state.get(f"pick_{pid}")]
        if len(chosen) < 4:
            st.caption(f"Đã chọn {len(chosen)}/4 người")
        elif len(chosen) > 4:
            st.warning(f"Đã chọn {len(chosen)} người, bỏ bớt cho đủ 4.")
        else:
            options = ranked_splits(chosen)
            if not options:
                st.error("Bộ 4 người này không hợp lệ. Chỉ được 4 nam, 4 nữ hoặc "
                         "2 nam + 2 nữ (mỗi đội 1 nam 1 nữ).")
            else:
                def label(i):
                    s = options[i]
                    tag = " (gợi ý)" if i == 0 else ""
                    return (f"{s['type']}: {team_txt(s['a'])}  vs  {team_txt(s['b'])}{tag}  "
                            f"| lệch {s['diff']}, trùng cặp {s['partner']}, trùng đối thủ {s['opp']}")

                pick = st.radio("Chia cặp", range(len(options)), format_func=label,
                                key="split_" + "_".join(sorted(chosen)))
                s = options[pick]
                st.button("Vào sân", type="primary", on_click=start_match,
                          args=(court, s["a"], s["b"]), use_container_width=True)

    else:  # Gợi ý tự động
        idx = st.session_state["sug_idx"] % len(sugs)
        s = sugs[idx]
        court = st.selectbox("Vào sân", free, format_func=lambda k: f"Sân {k}", key="auto_court")
        with st.container(border=True):
            st.markdown(f"**Đội A:** {team_txt(s['a'])}")
            st.markdown(f"**Đội B:** {team_txt(s['b'])}")
            games = ", ".join(str(P[i]["games"]) for i in s["ids"])
            st.caption(
                f"{s['type']}. Phương án {idx + 1}/{len(sugs)}. Lệch sức mạnh hai đội: {s['diff']}. "
                f"Số trận đã đánh của 4 người: {games}. "
                f"Cặp cùng đội đã trùng: {s['partner']} lần. Đối thủ đã trùng: {s['opp']} lần."
            )
        c1, c2 = st.columns([3, 2])
        c1.button("Xếp vào sân", type="primary", on_click=start_match,
                  args=(court, s["a"], s["b"]), use_container_width=True)
        c2.button("Phương án khác", on_click=next_suggestion, use_container_width=True)

# ---- Tab 2: người chơi
with tab_players:
    st.subheader(f"Danh sách ({len(P)}/{MAX_PLAYERS})")
    with st.form("add_form", clear_on_submit=True):
        c1, c2, c3 = st.columns([3, 2, 3])
        c1.text_input("Tên", key="new_name", placeholder="Nhập tên rồi Enter")
        c2.selectbox("Giới tính", GENDERS, key="new_gender")
        c3.selectbox("Trình", list(LEVELS), index=1, key="new_level")
        st.form_submit_button("Thêm người", on_click=add_player,
                              disabled=len(P) >= MAX_PLAYERS, use_container_width=True)

    busy = playing_ids()
    for pid, p in P.items():
        c1, c2, c3, c4, c5 = st.columns([3, 2, 3, 2, 2])
        status = "đang đánh" if pid in busy else ("nghỉ" if p["rest"] else "chờ")
        c1.markdown(f"**{p['name']}**  \n{status}, {p['games']} trận")
        c2.selectbox("Giới tính", GENDERS, index=GENDERS.index(p["gender"]),
                     key=f"gd_{pid}", on_change=set_gender, args=(pid,),
                     label_visibility="collapsed")
        c3.selectbox("Trình", list(LEVELS), index=list(LEVELS).index(p["level"]),
                     key=f"lv_{pid}", on_change=set_level, args=(pid,),
                     label_visibility="collapsed")
        c4.toggle("Nghỉ", value=p["rest"], key=f"rest_{pid}", on_change=set_rest, args=(pid,))
        c5.button("Xóa", key=f"del_{pid}", on_click=delete_player, args=(pid,))

    with st.expander("Cài đặt buổi chơi"):
        min_courts = max([int(k) for k, m in d["courts"].items() if m] + [1])
        st.number_input("Số sân", min_value=min_courts, max_value=MAX_COURTS,
                        value=max(len(d["courts"]), min_courts), key="n_courts",
                        on_change=set_courts)
        sure = st.checkbox("Tôi chắc chắn muốn xóa dữ liệu", key="sure_reset")
        r1, r2 = st.columns(2)
        r1.button("Làm lại số trận", on_click=reset_stats, disabled=not sure,
                  help="Giữ danh sách người, đưa số trận và lịch sử về 0",
                  use_container_width=True)
        r2.button("Xóa tất cả", on_click=reset_all, disabled=not sure,
                  help="Xóa cả danh sách người", use_container_width=True)

# ---- Tab 3: thống kê
with tab_stats:
    where = {}
    for k, m in d["courts"].items():
        if m:
            for i in m["a"] + m["b"]:
                where[i] = f"Đang đánh (sân {k})"
    wait_order = {pid: n + 1 for n, pid in enumerate(waiting_ids())}
    rows = []
    for pid, p in P.items():
        if pid in where:
            status = where[pid]
        elif p["rest"]:
            status = "Nghỉ"
        else:
            status = f"Chờ (thứ {wait_order[pid]})"
        rows.append({"Tên": p["name"], "Giới tính": p["gender"], "Trình": p["level"], "Trạng thái": status, "Số trận": p["games"]})
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    else:
        st.info("Chưa có người chơi.")

    st.subheader("Các trận đã xong")
    if d["history"]:
        for h in reversed(d["history"]):
            st.write(f"{h['time']} · Sân {h['court']} · {h.get('type') or ''} · "
                     f"{' + '.join(h['a'])}  vs  {' + '.join(h['b'])}")
    else:
        st.caption("Chưa có trận nào.")