import os

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")

st.set_page_config(page_title="Field Service Copilot")
st.title("Field Service Copilot")

try:
    st.caption(f"API: {requests.get(f'{API_URL}/health', timeout=5).json()['status']}")
except requests.RequestException as error:
    st.error(f"API not reachable at {API_URL}: {error}")
