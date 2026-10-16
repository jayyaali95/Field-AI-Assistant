"""Technician UI. Talks only to the API; never touches the database or the documents."""
import os

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")
CERT_NAMES = {"cert-hv": "HV", "cert-refrigerant": "refrigerant", "cert-confined-space": "confined space"}

st.set_page_config(page_title="Field Service Copilot", page_icon="🔧")


def api(method: str, path: str, **kwargs):
    """Call the API; show the error and stop the page if it fails."""
    try:
        response = requests.request(method, f"{API_URL}{path}", timeout=300, **kwargs)
    except requests.RequestException as error:
        st.error(f"Can't reach the API at {API_URL}: {error}")
        st.stop()
    if not response.ok:
        detail = response.json().get("detail", response.text) if "json" in response.headers.get("content-type", "") else response.text
        st.error(f"{response.status_code}: {detail}")
        return None
    return response.json()


def show_work_order(work_order: dict) -> None:
    draft = work_order["draft"]
    st.markdown(f"**Work order #{work_order['id']}** · status **{work_order['status']}**")
    rows = {
        "Asset": draft["asset_id"],
        "Fault code": draft["fault_code"],
        "Summary": draft["summary"],
        "Certification required": ", ".join(draft["certification_required"]),
        "Priority": draft["priority"],
        "Parts": ", ".join(draft["parts"]),
        "Estimated hours": draft["estimated_hours"],
        "Campaign code": draft["campaign_code"],
    }
    st.markdown("\n".join(f"- **{k}:** {v}" for k, v in rows.items() if v not in (None, "", [])))
    if draft["work_steps"]:
        st.markdown("**Work steps**\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(draft["work_steps"], 1)))
    if work_order["status"] != "draft":
        decided_by = names.get(work_order["decided_by"], work_order["decided_by"])
        decided_at = work_order["decided_at"][:16].replace("T", " ")
        st.caption(f"{work_order['status'].capitalize()} by {decided_by} at {decided_at} UTC")


# ---------- who is asking ----------

users = api("GET", "/users")
if not users:
    st.stop()
by_id = {u["id"]: u for u in users}
names = {u["id"]: u["name"] for u in users}
user_id = st.sidebar.selectbox(
    "Technician", list(by_id), format_func=lambda i: f"{by_id[i]['name']} ({by_id[i]['title']})"
)
user = by_id[user_id]
st.sidebar.markdown("**Groups**\n" + "\n".join(f"- `{g}`" for g in user["groups"]))
if user["is_lead"]:
    st.sidebar.success("Team lead: can approve work orders")

st.title("Field Service Copilot")
ask_tab, orders_tab = st.tabs(["Ask", "Work orders"])

# ---------- ask ----------

with ask_tab:
    with st.form("ask"):
        question = st.text_area("What's going on?", placeholder="TAC-C-0143 tripped on E-31. How do I reset it?")
        submitted = st.form_submit_button("Ask", type="primary")
    if submitted and question.strip():
        with st.spinner("Searching your documents..."):
            result = api("POST", "/ask", json={"user_id": user_id, "question": question})
        if result:
            st.session_state.result = {"user_id": user_id, "question": question, **result}

    result = st.session_state.get("result")
    if result and result["user_id"] == user_id:  # don't show one user's answer to another
        if result["missing_certifications"]:
            certs = ", ".join(CERT_NAMES.get(c, c) for c in result["missing_certifications"])
            st.warning(f"Part of this needs {certs} certification, which you don't hold. A certified technician has to do that work.")
        if not result["covered"]:
            st.info("No document you have access to covers this.")
        st.markdown(result["answer"])

        if result["citations"]:
            st.subheader("Sources")
            for c in result["citations"]:
                with st.expander(f"[{c['marker']}] {c['title']} · {c['location']}"):
                    st.caption(f"{c['source_system']} · {c['path']}")
                    st.text(c["excerpt"])

        if result["work_order"]:
            st.subheader("Draft work order")
            with st.container(border=True):
                show_work_order(result["work_order"])
                st.caption("Saved as a draft. Nothing is sent until a team lead approves it in the Work orders tab.")

# ---------- work orders ----------

with orders_tab:
    if not user["is_lead"]:
        st.caption("Your work orders. Only team leads can approve or reject; switch to Lena Hoffmann or Kenji Watanabe.")
    work_orders = api("GET", "/work-orders", params={"user_id": user_id}) or []
    if not work_orders:
        st.write("No work orders yet.")
    for work_order in work_orders:
        with st.container(border=True):
            show_work_order(work_order)
            requester = names.get(work_order["requested_by"], work_order["requested_by"])
            st.caption(f"Requested by {requester}: \"{work_order['question']}\"")
            if work_order["status"] == "draft" and user["is_lead"]:
                approve, reject = st.columns(2)
                for column, label, value in ((approve, "Approve", True), (reject, "Reject", False)):
                    if column.button(label, key=f"{label}-{work_order['id']}", type="primary" if value else "secondary"):
                        if api("POST", f"/work-orders/{work_order['id']}/decision", json={"user_id": user_id, "approve": value}):
                            st.rerun()
