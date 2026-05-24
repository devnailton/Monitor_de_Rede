import json
import time
from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from pinger import PingMonitor
from storage import Storage

st.set_page_config(page_title="Monitor de Rede", layout="wide")

WINDOWS = {
    "Últimos 5 min": timedelta(minutes=5),
    "Última hora": timedelta(hours=1),
    "Últimas 24h": timedelta(hours=24),
    "Últimos 7 dias": timedelta(days=7),
}


@st.cache_resource
def get_monitor() -> PingMonitor:
    storage = Storage()
    m = PingMonitor(interval=5.0, timeout_ms=1000, storage=storage)
    m.start()
    return m


monitor = get_monitor()
storage = monitor.storage

st.title("Monitoramento de Latência da Rede")

with st.sidebar:
    st.header("Configurações")
    monitor.interval = st.number_input("Intervalo (s)", 1.0, 60.0, monitor.interval, 1.0)
    monitor.timeout_ms = st.number_input("Timeout (ms)", 100, 5000, monitor.timeout_ms, 100)
    alert_threshold = st.number_input("Alerta de latência (ms)", 1, 5000, 100, 10)
    refresh_s = st.number_input("Atualizar UI a cada (s)", 1, 30, 5, 1)
    window_label = st.selectbox("Janela do gráfico", list(WINDOWS.keys()), index=1)
    sound_on = st.checkbox("Alerta sonoro", value=True)

    st.divider()
    st.header("Adicionar equipamento")
    with st.form("add_device", clear_on_submit=True):
        name = st.text_input("Nome")
        host = st.text_input("IP ou hostname")
        if st.form_submit_button("Adicionar") and name and host:
            monitor.add_device(name.strip(), host.strip())
            st.success(f"{name} adicionado.")

    devices = monitor.list_devices()
    active_names = {d.name for d in devices}

    st.divider()
    st.header("Equipamentos salvos")
    known = [(n, h) for n, h in storage.list_known_devices() if n not in active_names]
    if known:
        labels = [f"{n} ({h})" for n, h in known]
        picked = st.multiselect("Adicionar do histórico", labels)
        if st.button("Adicionar selecionados") and picked:
            for label in picked:
                idx = labels.index(label)
                n, h = known[idx]
                monitor.add_device(n, h)
            st.rerun()
        forget = st.selectbox("Esquecer equipamento", [""] + [n for n, _ in known])
        if st.button("Esquecer") and forget:
            storage.forget_known_device(forget)
            st.rerun()
    else:
        st.caption("Nenhum equipamento no histórico ainda.")

    st.divider()
    st.header("Grupos")
    groups = storage.list_groups()
    if groups:
        chosen = st.selectbox("Selecionar grupo", [""] + groups)
        col_a, col_b, col_c = st.columns(3)
        if col_a.button("Testar grupo") and chosen:
            for d in monitor.list_devices():
                monitor.remove_device(d.name)
            for n, h in storage.get_group(chosen):
                monitor.add_device(n, h)
            st.rerun()
        if col_b.button("Adicionar") and chosen:
            for n, h in storage.get_group(chosen):
                monitor.add_device(n, h)
            st.rerun()
        if col_c.button("Excluir") and chosen:
            storage.delete_group(chosen)
            st.rerun()

    with st.form("save_group", clear_on_submit=True):
        new_group = st.text_input("Salvar equipamentos atuais como grupo")
        if st.form_submit_button("Salvar grupo") and new_group and devices:
            storage.save_group(new_group.strip(), [(d.name, d.host) for d in devices])
            st.success(f"Grupo '{new_group}' salvo.")

    if devices:
        st.divider()
        st.header("Principais")
        current_primaries = storage.get_primaries()
        new_primaries = st.multiselect(
            "Equipamentos em destaque (gráfico largo no topo)",
            [d.name for d in devices],
            default=[n for n in current_primaries if n in {d.name for d in devices}],
        )
        if set(new_primaries) != current_primaries:
            for d in devices:
                storage.set_primary(d.name, d.name in new_primaries)
            st.rerun()

        st.divider()
        st.header("Editar")
        edit_target = st.selectbox(
            "Equipamento para editar", [""] + [d.name for d in devices], key="edit_sel"
        )
        if edit_target:
            current = next(d for d in devices if d.name == edit_target)
            with st.form("edit_device", clear_on_submit=False):
                new_name = st.text_input("Novo nome", value=current.name)
                new_host = st.text_input("Novo IP/hostname", value=current.host)
                if st.form_submit_button("Salvar alterações"):
                    try:
                        monitor.edit_device(edit_target, new_name.strip(), new_host.strip())
                        st.success("Equipamento atualizado.")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

        st.divider()
        st.header("Remover")
        to_remove = st.selectbox("Equipamento ativo", [""] + [d.name for d in devices])
        if st.button("Remover") and to_remove:
            monitor.remove_device(to_remove)
            st.rerun()

    st.divider()
    st.header("Exportar / Importar")
    config_json = json.dumps(storage.export_config(), indent=2, ensure_ascii=False)
    st.download_button(
        "Exportar configurações",
        data=config_json,
        file_name=f"monitor-rede-config-{datetime.now():%Y%m%d-%H%M}.json",
        mime="application/json",
    )
    uploaded = st.file_uploader("Importar configurações (.json)", type=["json"])
    if uploaded is not None:
        mode = st.radio(
            "Modo de importação",
            ["Mesclar", "Substituir tudo"],
            horizontal=True,
            key="import_mode",
        )
        if st.button("Aplicar importação"):
            try:
                data = json.loads(uploaded.getvalue().decode("utf-8"))
                storage.import_config(data, replace=(mode == "Substituir tudo"))
                st.cache_resource.clear()
                st.success("Configurações importadas. Recarregue para ver tudo aplicado.")
                st.rerun()
            except Exception as e:
                st.error(f"Falha ao importar: {e}")

    st.divider()
    if st.button("Purgar amostras > 30 dias"):
        storage.purge_older_than(30)
        st.success("Histórico antigo removido.")

since = datetime.now() - WINDOWS[window_label]
history = storage.query_window(since)
snapshot = monitor.snapshot()
primaries = storage.get_primaries()

if not snapshot and not history:
    st.info("Adicione um equipamento no painel lateral para começar.")
else:
    over_threshold = {}

    if "alert_ts" not in st.session_state:
        st.session_state.alert_ts = {}

    def render_device(name: str, samples, height: int) -> None:
        latest = next((v for _, v in reversed(samples) if v is not None), None)
        window_samples = history.get(name, [])
        total = len(window_samples)
        lost = sum(1 for _, v in window_samples if v is None)
        loss_pct = (lost / total * 100) if total else 0.0
        label = f"⭐ {name}" if name in primaries else name
        delta_text = f"perda {loss_pct:.1f}%" if loss_pct > 0 else "perda 0%"
        delta_color = "inverse" if loss_pct > 0 else "off"

        if latest is not None and latest > alert_threshold:
            st.session_state.alert_ts[name] = time.time()
            over_threshold[name] = latest

        alert_active = (
            name in st.session_state.alert_ts
            and (time.time() - st.session_state.alert_ts[name]) < 3
        )

        if latest is None:
            st.metric(label, "—", delta_text, delta_color=delta_color)
        else:
            st.metric(label, f"{latest:.1f} ms", delta_text, delta_color=delta_color)

        if window_samples:
            df = pd.DataFrame(window_samples, columns=["timestamp", "latency"]).dropna()
            line_color = "#ff3333" if alert_active else "#1f77b4"
            stroke_width = 3 if alert_active else 2
            background = "#3a0d0d" if alert_active else None
            line = (
                alt.Chart(df)
                .mark_line(color=line_color, strokeWidth=stroke_width)
                .encode(
                    x=alt.X("timestamp:T", title=None),
                    y=alt.Y("latency:Q", title="ms"),
                )
            )
            chart = line.properties(height=height)
            if background:
                chart = chart.configure_view(fill=background)
            st.altair_chart(chart, use_container_width=True)
        else:
            st.caption("Sem dados na janela selecionada ainda.")

    primary_names = [n for n in snapshot if n in primaries]
    other_names = [n for n in snapshot if n not in primaries]

    for name in primary_names:
        render_device(name, snapshot[name], height=350)
        st.divider()

    if other_names:
        per_row = 2
        for i in range(0, len(other_names), per_row):
            row = other_names[i : i + per_row]
            cols = st.columns(len(row))
            for col, name in zip(cols, row):
                with col:
                    render_device(name, snapshot[name], height=220)

    if over_threshold:
        st.warning(
            "Acima do limite: "
            + ", ".join(f"{n} ({v:.0f} ms)" for n, v in over_threshold.items())
        )
        if sound_on:
            st.html(
                """
                <audio id="alert-beep" src="https://actions.google.com/sounds/v1/alarms/beep_short.ogg"></audio>
                <script>
                  (function() {
                    var audio = document.getElementById('alert-beep');
                    if (!audio) return;
                    var count = 0;
                    function play() {
                      audio.currentTime = 0;
                      audio.play().catch(function(){});
                      count++;
                      if (count < 5) setTimeout(play, 600);
                    }
                    play();
                  })();
                </script>
                """
            )

st.caption(f"Atualização automática a cada {refresh_s}s.")
st.html(f"<meta http-equiv='refresh' content='{refresh_s}'>")
