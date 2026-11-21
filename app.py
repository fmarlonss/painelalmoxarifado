import calendar
import datetime
import io
import unicodedata

import pandas as pd
import plotly.express as px
import streamlit as st
from fpdf import FPDF

import auth
import dados_google

# Configuração inicial da página
st.set_page_config(
    page_title="Painel de Controle de Saídas", page_icon="📦", layout="wide"
)

# --- LOGIN: nada abaixo roda sem autenticação ---
auth.exigir_login()

# Paleta institucional
COR_PRIMARIA = "#094780"
COR_SECUNDARIA = "#417690"
COR_DESTAQUE = "#f5dd5d"

MESES_ABREV_PT = {
    1: "Jan",
    2: "Fev",
    3: "Mar",
    4: "Abr",
    5: "Mai",
    6: "Jun",
    7: "Jul",
    8: "Ago",
    9: "Set",
    10: "Out",
    11: "Nov",
    12: "Dez",
}


def remover_acentos(texto):
    """Normaliza textos para busca por similaridade (ignora acentos e caixa alta/baixa)."""
    if not isinstance(texto, str):
        return ""
    texto = texto.replace("\ufeff", "")
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join([c for c in nfkd if not unicodedata.combining(c)]).lower().strip()


def encontrar_coluna_flex(df, candidatos):
    """Localiza coluna ignorando acentos, caixa alta/baixa e espaços extras."""
    mapa = {remover_acentos(c): c for c in df.columns}
    for cand in candidatos:
        chave = remover_acentos(cand)
        if chave in mapa:
            return mapa[chave]
    for cand in candidatos:
        chave = remover_acentos(cand)
        for col_norm, col_real in mapa.items():
            if chave and chave in col_norm:
                return col_real
    return None


def limpar_numero_br(serie):
    """Converte strings numéricas no padrão brasileiro (1.234,56 ou R$ 10,00) para float."""
    s = (
        serie.astype(str)
        .str.replace("R$", "", regex=False)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
        .str.strip()
        .replace(["", "nan", "None", "NaN"], "0")
    )
    return pd.to_numeric(s, errors="coerce").fillna(0.0).astype(float)


def limpar_decimal_previsao(serie):
    """Converte valores numéricos (BR ou US) com segurança para float."""
    s = serie.astype(str).str.replace("R$", "", regex=False).str.strip()

    tem_ponto_e_virgula = s.str.contains(r"\.", regex=True) & s.str.contains(
        ",", regex=False
    )
    s = s.where(
        ~tem_ponto_e_virgula,
        s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False),
    )

    s = s.str.replace(",", ".", regex=False)
    s = s.str.replace(r"[^0-9.\-]", "", regex=True)

    return pd.to_numeric(s, errors="coerce").fillna(0.0).astype(float)


def limpar_texto_pdf(texto):
    """Garante compatibilidade com fontes padrão Latin-1 no FPDF."""
    if texto is None:
        return ""
    s = str(texto).strip()
    return s.encode("latin-1", errors="replace").decode("latin-1")


@st.cache_data
def carregar_dados():
    """Baixa as saídas direto do Google Planilhas e trata todas as colunas."""
    try:
        df = dados_google.obter_saidas()
    except Exception as e:
        st.error(f"⚠️ Não foi possível carregar os dados do Google Planilhas: {e}")
        st.stop()

    df.columns = [c.replace("\ufeff", "").strip() for c in df.columns]

    for col in df.columns:
        df[col] = df[col].fillna("").astype(str).str.strip()

    df["Data_dt"] = pd.to_datetime(
        df["Data da Saída"], dayfirst=True, errors="coerce"
    )
    df = df.dropna(subset=["Data_dt"]).copy()
    df["Ano"] = df["Data_dt"].dt.year.astype(str)
    df["Mes_Num"] = df["Data_dt"].dt.month
    df["Mes_Abrev"] = df["Mes_Num"].map(MESES_ABREV_PT)

    df["Quantidade_num"] = limpar_numero_br(df["Quantidade"])
    df["Unitario_num"] = limpar_numero_br(df["Unitário"])
    df["Total_num"] = limpar_numero_br(df["Total"])

    df["Código"] = df["Código"].str.zfill(2)
    df["Grupo"] = df["Código"].str[:2]
    df["Especificacao_norm"] = df["Especificação"].apply(remover_acentos)

    return df


@st.cache_data
def carregar_inventario_previsao():
    """Baixa o inventário com previsão e retorna um DataFrame padronizado."""
    try:
        df_raw_inv = dados_google.obter_inventario_previsao()
    except Exception:
        return None

    if df_raw_inv is None or df_raw_inv.empty:
        return None

    df_raw_inv.columns = [c.replace("\ufeff", "").strip() for c in df_raw_inv.columns]

    for col in df_raw_inv.columns:
        df_raw_inv[col] = df_raw_inv[col].fillna("").astype(str).str.strip()

    col_mat = encontrar_coluna_flex(
        df_raw_inv, ["Material", "Código", "Codigo", "Cod. Material", "Cod Material"]
    )
    col_desc = encontrar_coluna_flex(
        df_raw_inv,
        ["Descrição", "Descricao", "Especificação", "Especificacao", "Item"],
    )
    col_emb = encontrar_coluna_flex(
        df_raw_inv, ["Emb. padrão", "Emb. padrao", "Embalagem", "Emb", "Unidade"]
    )
    col_est = encontrar_coluna_flex(
        df_raw_inv, ["Estoque(D)", "Estoque_atual", "Estoque atual", "Estoque"]
    )
    col_prev = encontrar_coluna_flex(
        df_raw_inv,
        [
            "Previsão de duração (meses)",
            "Previsao de duracao (meses)",
            "Previsão",
            "Previsao",
            "Duração",
            "Duracao",
        ],
    )

    df_out = pd.DataFrame()
    df_out["Material"] = (
        df_raw_inv[col_mat] if col_mat else df_raw_inv.iloc[:, 0]
    )
    df_out["Descrição"] = (
        df_raw_inv[col_desc]
        if col_desc
        else (df_raw_inv.iloc[:, 1] if len(df_raw_inv.columns) > 1 else "")
    )
    df_out["Emb. padrão"] = df_raw_inv[col_emb] if col_emb else ""
    df_out["Estoque_exib_num"] = (
        limpar_decimal_previsao(df_raw_inv[col_est]) if col_est else 0.0
    )
    df_out["Previsao_num"] = (
        limpar_decimal_previsao(df_raw_inv[col_prev]) if col_prev else 0.0
    )

    return df_out.sort_values("Previsao_num", ascending=True).reset_index(
        drop=True
    )


def formatar_moeda(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar_inteiro(valor):
    return f"{int(valor):,}".replace(",", ".")


def formatar_decimal_br(valor, casas=1):
    fmt = f"{{:,.{casas}f}}".format(float(valor))
    return fmt.replace(",", "X").replace(".", ",").replace("X", ".")


class PDFPrevisao(FPDF):
    def __init__(self, subtitulo_filtro):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.subtitulo_filtro = limpar_texto_pdf(subtitulo_filtro)
        self.set_auto_page_break(auto=True, margin=18)

    def header(self):
        self.set_fill_color(9, 71, 128)
        self.rect(0, 0, 210, 28, style="F")

        self.set_fill_color(245, 221, 93)
        self.rect(0, 28, 210, 1.8, style="F")

        self.set_xy(12, 7)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 7, limpar_texto_pdf("PREVISÃO DE DURAÇÃO DE ESTOQUE"), ln=1)

        self.set_xy(12, 15)
        self.set_font("Helvetica", "", 9)
        data_emissao = dados_google.momento_atualizacao().strftime("%d/%m/%Y às %H:%M")
        self.cell(
            0,
            5,
            limpar_texto_pdf(
                f"Emissão: {data_emissao}  |  Escopo: {self.subtitulo_filtro}"
            ),
            ln=1,
        )

        self.set_y(35)
        self.set_text_color(30, 30, 30)

    def footer(self):
        self.set_y(-13)
        self.set_draw_color(210, 215, 220)
        self.line(12, self.get_y() - 2, 198, self.get_y() - 2)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(110, 110, 110)
        self.cell(
            0,
            8,
            limpar_texto_pdf(
                f"Monitoramento de Estoque - Página {self.page_no()}/{{nb}}"
            ),
            align="R",
        )


def gerar_pdf_previsao(df_tabela, apenas_criticos):
    """Gera PDF profissional contendo apenas Material, Descrição, Emb., Estoque e Previsão."""
    escopo = (
        "Materiais Críticos (< 4 meses)"
        if apenas_criticos
        else "Todos os Materiais (Ordem Crescente de Duração)"
    )
    pdf = PDFPrevisao(subtitulo_filtro=escopo)
    pdf.alias_nb_pages()
    pdf.add_page()

    total_itens = len(df_tabela)
    criticos_qtd = int((df_tabela["Previsao_num"] < 4).sum())
    pdf.set_fill_color(240, 244, 248)
    pdf.set_draw_color(200, 210, 220)
    pdf.rect(12, pdf.get_y(), 186, 10, style="DF")
    pdf.set_xy(15, pdf.get_y() + 2.5)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(9, 71, 128)
    pdf.cell(
        0,
        5,
        limpar_texto_pdf(
            f"Total de itens listados: {total_itens}   |   Itens críticos (< 4 meses): {criticos_qtd}"
        ),
    )
    pdf.ln(14)

    larguras = [22, 92, 20, 22, 20]
    cabecalhos = ["Material", "Descrição", "Emb.", "Estoque", "Previsão"]
    alinhamentos = ["C", "L", "C", "R", "R"]

    def desenhar_cabecalho_tabela():
        pdf.set_fill_color(65, 118, 144)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 8.5)
        pdf.set_draw_color(65, 118, 144)
        x_ini = 12
        pdf.set_x(x_ini)
        for col_nome, w, align in zip(cabecalhos, larguras, alinhamentos):
            pdf.cell(
                w, 7, limpar_texto_pdf(col_nome), border=1, align=align, fill=True
            )
        pdf.ln(7)

    desenhar_cabecalho_tabela()

    pdf.set_font("Helvetica", "", 8)
    for idx, row in df_tabela.iterrows():
        mat_txt = limpar_texto_pdf(row.get("Material", ""))
        desc_txt = limpar_texto_pdf(row.get("Descrição", ""))
        emb_txt = limpar_texto_pdf(row.get("Emb. padrão", ""))
        est_txt = limpar_texto_pdf(
            formatar_decimal_br(row.get("Estoque_exib_num", 0.0), 1)
        )
        prev_txt = limpar_texto_pdf(
            f"{formatar_decimal_br(row.get('Previsao_num', 0.0), 1)} m"
        )

        largura_util_desc = larguras[1] - 2
        linhas_desc = max(
            1, int(pdf.get_string_width(desc_txt) / max(1, largura_util_desc)) + 1
        )
        altura_linha = max(6.5, linhas_desc * 4.2 + 2)

        if pdf.get_y() + altura_linha > 276:
            pdf.add_page()
            desenhar_cabecalho_tabela()
            pdf.set_font("Helvetica", "", 8)

        y_antes = pdf.get_y()
        x_atual = 12

        is_critico = float(row.get("Previsao_num", 0.0)) < 4.0
        if is_critico:
            pdf.set_fill_color(255, 249, 219)
        elif idx % 2 == 1:
            pdf.set_fill_color(248, 250, 252)
        else:
            pdf.set_fill_color(255, 255, 255)

        pdf.set_draw_color(215, 220, 225)
        pdf.set_text_color(25, 25, 25)

        pdf.set_xy(x_atual, y_antes)
        pdf.cell(
            larguras[0], altura_linha, mat_txt, border=1, align="C", fill=True
        )
        x_atual += larguras[0]

        pdf.set_xy(x_atual, y_antes)
        pdf.rect(x_atual, y_antes, larguras[1], altura_linha, style="DF")
        pdf.set_xy(x_atual + 1, y_antes + 1)
        pdf.multi_cell(larguras[1] - 2, 4.0, desc_txt, border=0, align="L")
        x_atual += larguras[1]

        pdf.set_xy(x_atual, y_antes)
        pdf.cell(
            larguras[2], altura_linha, emb_txt, border=1, align="C", fill=True
        )
        x_atual += larguras[2]

        pdf.set_xy(x_atual, y_antes)
        pdf.cell(
            larguras[3], altura_linha, est_txt, border=1, align="R", fill=True
        )
        x_atual += larguras[3]

        pdf.set_xy(x_atual, y_antes)
        if is_critico:
            pdf.set_font("Helvetica", "B", 8)
            pdf.set_text_color(150, 25, 25)
        pdf.cell(
            larguras[4], altura_linha, prev_txt, border=1, align="R", fill=True
        )
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(25, 25, 25)

        pdf.set_y(y_antes + altura_linha)

    pdf_bytes = pdf.output()
    if isinstance(pdf_bytes, (bytes, bytearray)):
        return io.BytesIO(bytes(pdf_bytes))
    return io.BytesIO(pdf_bytes.encode("latin-1"))


# --- CARREGA OS DADOS ---
df_raw = carregar_dados()
df_inv_raw = carregar_inventario_previsao()


# --- SIDEBAR: USUÁRIO + ATUALIZAÇÃO + FILTROS GLOBAIS ---
auth.painel_usuario_sidebar()
if st.sidebar.button("🔄 Atualizar dados", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption(
    f"Última atualização: {dados_google.momento_atualizacao():%d/%m/%Y %H:%M}"
)
st.sidebar.markdown("---")

st.sidebar.title("⚙️ Filtros do Painel")

data_min = df_raw["Data_dt"].min().date()
data_max = df_raw["Data_dt"].max().date()

periodo = st.sidebar.date_input(
    "Período da Saída",
    value=(data_min, data_max),
    min_value=data_min,
    max_value=data_max,
    format="DD/MM/YYYY",
)

grupos_disp = sorted(df_raw["Grupo"].unique())
grupos_sel = st.sidebar.multiselect("Grupo do Material", grupos_disp)

graus_disp = sorted([g for g in df_raw["GRAU"].unique() if g])
graus_sel = st.sidebar.multiselect("Grau", graus_disp)

codigos_disp = sorted([c for c in df_raw["Código"].unique() if c])
codigos_sel = st.sidebar.multiselect("Código do Material", codigos_disp)

texto_busca = st.sidebar.text_input(
    "Nome do Material (busca aproximada)",
    placeholder="Ex: papel a4, caneta, toner...",
)

col_setor = (
    "REFERÊNCIA DO MUNICÍPIO"
    if "REFERÊNCIA DO MUNICÍPIO" in df_raw.columns
    and df_raw["REFERÊNCIA DO MUNICÍPIO"].str.strip().any()
    else "ENTRÂNCIA"
)
setores_disp = sorted([s for s in df_raw[col_setor].unique() if s])
setores_sel = st.sidebar.multiselect(f"Setor ({col_setor})", setores_disp)

municipios_disp = sorted([m for m in df_raw["MUNICÍPIO"].unique() if m])
municipios_sel = st.sidebar.multiselect("Município", municipios_disp)

destinos_disp = sorted([d for d in df_raw["DESTINO"].unique() if d])
destinos_sel = st.sidebar.multiselect("Destino", destinos_disp)


# --- APLICAÇÃO DOS FILTROS ---
df = df_raw.copy()

if isinstance(periodo, tuple) and len(periodo) == 2:
    df = df[
        (df["Data_dt"].dt.date >= periodo[0])
        & (df["Data_dt"].dt.date <= periodo[1])
    ]

if grupos_sel:
    df = df[df["Grupo"].isin(grupos_sel)]

if graus_sel:
    df = df[df["GRAU"].isin(graus_sel)]

if codigos_sel:
    df = df[df["Código"].isin(codigos_sel)]

if texto_busca.strip():
    termos = remover_acentos(texto_busca).split()
    for termo in termos:
        df = df[df["Especificacao_norm"].str.contains(termo, regex=False)]

if setores_sel:
    df = df[df[col_setor].isin(setores_sel)]

if municipios_sel:
    df = df[df["MUNICÍPIO"].isin(municipios_sel)]

if destinos_sel:
    df = df[df["DESTINO"].isin(destinos_sel)]


# --- ABAS PRINCIPAIS DO SISTEMA ---
aba_painel, aba_comparativo, aba_calendario, aba_previsao = st.tabs(
    [
        "📊 Painel Principal de Indicadores",
        "📈 Comparativo Anual & Detalhamento",
        "📅 Calendário de Saídas",
        "⏳ Previsão de Duração",
    ]
)


# ==========================================
# ABA 1: PAINEL PRINCIPAL
# ==========================================
with aba_painel:
    st.title("📊 Painel Analítico de Saídas de Materiais")
    st.markdown(
        "Acompanhamento quantitativo e financeiro das movimentações de estoque."
    )

    if df.empty:
        st.warning("Nenhum registro encontrado para os filtros selecionados.")
    else:
        col1, col2, col3, col4 = st.columns(4)

        total_valor = df["Total_num"].sum()
        total_itens_saidos = df["Quantidade_num"].sum()
        total_materiais_distintos = df["Código"].nunique()
        total_pedidos = df["N° da Saída"].nunique()

        col1.metric("💰 Total Valor Enviado", formatar_moeda(total_valor))
        col2.metric("📦 Total de Saídas (Qtd)", formatar_inteiro(total_itens_saidos))
        col3.metric(
            "🏷️ Materiais Distintos", formatar_inteiro(total_materiais_distintos)
        )
        col4.metric("🧾 Total de Pedidos Saídos", formatar_inteiro(total_pedidos))

        st.markdown("---")

        df_mensal = (
            df.groupby(["Mes_Num", "Mes_Abrev", "Ano"], as_index=False)
            .agg(
                Total_Saidas=("Quantidade_num", "sum"),
                Total_Valor=("Total_num", "sum"),
            )
            .sort_values(["Mes_Num", "Ano"])
        )

        st.subheader("📈 Comparativo Mensal de Saídas por Ano (Quantidade)")
        fig_qtd_mes = px.bar(
            df_mensal,
            x="Mes_Abrev",
            y="Total_Saidas",
            color="Ano",
            barmode="group",
            text=df_mensal["Total_Saidas"].apply(formatar_inteiro),
            color_discrete_sequence=[COR_PRIMARIA, COR_SECUNDARIA, "#2ca02c"],
        )
        fig_qtd_mes.update_traces(textposition="outside")
        fig_qtd_mes.update_layout(yaxis_title="Quantidade", xaxis_title="")
        st.plotly_chart(fig_qtd_mes, use_container_width=True)

        st.markdown("---")

        st.subheader("💵 Comparativo Mensal de Valor por Ano (R$)")
        fig_val_mes = px.bar(
            df_mensal,
            x="Mes_Abrev",
            y="Total_Valor",
            color="Ano",
            barmode="group",
            text=df_mensal["Total_Valor"].apply(formatar_moeda),
            color_discrete_sequence=[COR_PRIMARIA, COR_SECUNDARIA, "#2ca02c"],
        )
        fig_val_mes.update_traces(textposition="outside")
        fig_val_mes.update_layout(yaxis_title="Valor (R$)", xaxis_title="")
        st.plotly_chart(fig_val_mes, use_container_width=True)

        st.markdown("---")

        st.subheader("🔎 Top 7 Materiais com Maior Quantidade de Saída")

        df_mat = (
            df.groupby(["Código", "Especificação"], as_index=False)
            .agg(
                Quantidade=("Quantidade_num", "sum"),
                Valor_Total=("Total_num", "sum"),
                Frequencia_Saidas=("N° da Saída", "count"),
            )
            .sort_values("Quantidade", ascending=False)
            .head(7)
            .reset_index(drop=True)
        )

        df_mat_exib = df_mat.copy()
        df_mat_exib["Quantidade"] = df_mat_exib["Quantidade"].apply(
            formatar_inteiro
        )
        df_mat_exib["Valor_Total"] = df_mat_exib["Valor_Total"].apply(
            formatar_moeda
        )
        df_mat_exib.columns = [
            "Código",
            "Nome do Material",
            "Qtd. Saída",
            "Valor Saído (R$)",
            "Nº de Saídas",
        ]

        st.dataframe(
            df_mat_exib,
            use_container_width=True,
            hide_index=True,
            height=300,
        )

        st.markdown("---")

        c_grau, c_setor = st.columns(2)
        with c_grau:
            st.subheader("🥧 Quantitativo de Saídas por Grau")
            df_grau = (
                df.groupby("GRAU", as_index=False)
                .agg(
                    Quantidade=("Quantidade_num", "sum"),
                    Valor_Total=("Total_num", "sum"),
                )
                .sort_values("Quantidade", ascending=False)
            )
            fig_grau = px.pie(
                df_grau,
                names="GRAU",
                values="Quantidade",
                hole=0.4,
                color_discrete_sequence=px.colors.sequential.Blues_r,
                hover_data={"Valor_Total": ":,.2f"},
            )
            fig_grau.update_traces(textposition="inside", textinfo="percent+label")
            fig_grau.update_layout(
                height=400, margin=dict(t=10, b=10, l=10, r=10)
            )
            st.plotly_chart(fig_grau, use_container_width=True)

        with c_setor:
            st.subheader(f"🏢 Quantitativo de Saídas por Setor ({col_setor})")
            df_setor = (
                df.groupby(col_setor, as_index=False)
                .agg(
                    Quantidade=("Quantidade_num", "sum"),
                    Pedidos=("N° da Saída", "nunique"),
                    Valor_Total=("Total_num", "sum"),
                )
                .sort_values("Quantidade", ascending=False)
            )
            df_setor_exib = df_setor.copy()
            df_setor_exib["Quantidade"] = df_setor_exib["Quantidade"].apply(
                formatar_inteiro
            )
            df_setor_exib["Valor_Total"] = df_setor_exib["Valor_Total"].apply(
                formatar_moeda
            )
            df_setor_exib.columns = [
                "Setor",
                "Qtd. Saída",
                "Pedidos",
                "Valor Total (R$)",
            ]
            st.dataframe(
                df_setor_exib, use_container_width=True, hide_index=True, height=400
            )

        st.markdown("---")

        c_dest, c_unid = st.columns(2)
        with c_dest:
            st.subheader("📍 Quantitativo de Saídas por Destino")
            df_destino = (
                df.groupby("DESTINO", as_index=False)
                .agg(
                    Quantidade=("Quantidade_num", "sum"),
                    Pedidos=("N° da Saída", "nunique"),
                    Valor_Total=("Total_num", "sum"),
                )
                .sort_values("Quantidade", ascending=False)
            )
            df_destino_exib = df_destino.copy()
            df_destino_exib["Quantidade"] = df_destino_exib["Quantidade"].apply(
                formatar_inteiro
            )
            df_destino_exib["Valor_Total"] = df_destino_exib["Valor_Total"].apply(
                formatar_moeda
            )
            df_destino_exib.columns = [
                "Destino",
                "Qtd. Saída",
                "Pedidos",
                "Valor Total (R$)",
            ]
            st.dataframe(
                df_destino_exib, use_container_width=True, hide_index=True, height=400
            )

        with c_unid:
            st.subheader("📏 Total de Valor Saído por Unidade")
            df_unidade = (
                df.groupby("Unidade", as_index=False)
                .agg(
                    Valor_Total=("Total_num", "sum"),
                    Quantidade=("Quantidade_num", "sum"),
                )
                .sort_values("Valor_Total", ascending=False)
            )
            df_unidade_exib = df_unidade.copy()
            df_unidade_exib["Valor_Total"] = df_unidade_exib["Valor_Total"].apply(
                formatar_moeda
            )
            df_unidade_exib["Quantidade"] = df_unidade_exib["Quantidade"].apply(
                formatar_inteiro
            )
            df_unidade_exib.columns = [
                "Unidade",
                "Valor Total (R$)",
                "Qtd. Saída",
            ]
            st.dataframe(
                df_unidade_exib, use_container_width=True, hide_index=True, height=400
            )

        st.markdown("---")

        st.subheader("🗺️ Distribuição de Saídas por Município")
        df_mun = (
            df.groupby("MUNICÍPIO", dropna=False, as_index=False)
            .agg(
                Quantidade=("Quantidade_num", "sum"),
                Pedidos=("N° da Saída", "nunique"),
                Valor_Total=("Total_num", "sum"),
            )
            .sort_values("Quantidade", ascending=False)
        )

        df_mun_exib = df_mun[
            ["MUNICÍPIO", "Quantidade", "Pedidos", "Valor_Total"]
        ].copy()
        df_mun_exib["Quantidade"] = df_mun_exib["Quantidade"].apply(
            formatar_inteiro
        )
        df_mun_exib["Valor_Total"] = df_mun_exib["Valor_Total"].apply(
            formatar_moeda
        )
        df_mun_exib.columns = [
            "Município",
            "Qtd. Saída",
            "Pedidos",
            "Valor Total (R$)",
        ]
        st.dataframe(
            df_mun_exib, use_container_width=True, hide_index=True, height=450
        )


# ==========================================
# ABA 2: COMPARATIVO ANUAL E DETALHAMENTO
# ==========================================
with aba_comparativo:
    st.title("📈 Comparativo Anual e Evolução Mensal")
    st.markdown(
        "Análise detalhada do comportamento de saídas e valores ano a ano aplicando os mesmos filtros da barra lateral."
    )

    if df.empty:
        st.warning("Nenhum registro encontrado para os filtros selecionados.")
    else:
        st.subheader("📅 Comparativo Anual de Saídas (Quantidade e Valor)")
        df_anual = (
            df.groupby("Ano", as_index=False)
            .agg(
                Total_Quantidade=("Quantidade_num", "sum"),
                Total_Valor=("Total_num", "sum"),
            )
            .sort_values("Ano")
        )

        c_ano_1, c_ano_2 = st.columns(2)
        with c_ano_1:
            st.markdown("**Quantitativo Total de Saídas por Ano**")
            fig_bar_qtd_ano = px.bar(
                df_anual,
                x="Ano",
                y="Total_Quantidade",
                text=df_anual["Total_Quantidade"].apply(formatar_inteiro),
                color_discrete_sequence=[COR_PRIMARIA],
                labels={"Ano": "Ano", "Total_Quantidade": "Quantidade Total"},
            )
            fig_bar_qtd_ano.update_traces(textposition="outside")
            st.plotly_chart(fig_bar_qtd_ano, use_container_width=True)

        with c_ano_2:
            st.markdown("**Valor Total de Saídas por Ano (R$)**")
            fig_bar_val_ano = px.bar(
                df_anual,
                x="Ano",
                y="Total_Valor",
                text=df_anual["Total_Valor"].apply(formatar_moeda),
                color_discrete_sequence=[COR_SECUNDARIA],
                labels={"Ano": "Ano", "Total_Valor": "Valor Total (R$)"},
            )
            fig_bar_val_ano.update_traces(textposition="outside")
            st.plotly_chart(fig_bar_val_ano, use_container_width=True)

        st.markdown("---")

        st.subheader(
            "📊 Evolução Mensal Comparativa: Quantidade e Valor (Com Variações)"
        )
        st.caption("Compara o acumulado mês a mês com o mês anterior.")

        df_mensal_geral = (
            df.groupby(["Mes_Num", "Mes_Abrev"], as_index=False)
            .agg(
                Quantidade=("Quantidade_num", "sum"),
                Valor_Total=("Total_num", "sum"),
            )
            .sort_values("Mes_Num")
        )

        df_mensal_geral["Var_Qtd"] = df_mensal_geral["Quantidade"].diff()
        df_mensal_geral["Var_Qtd_Pct"] = (
            df_mensal_geral["Quantidade"].pct_change() * 100
        )
        df_mensal_geral["Var_Valor"] = df_mensal_geral["Valor_Total"].diff()
        df_mensal_geral["Var_Valor_Pct"] = (
            df_mensal_geral["Valor_Total"].pct_change() * 100
        )

        df_mensal_exib = pd.DataFrame()
        df_mensal_exib["Mês"] = df_mensal_geral["Mes_Abrev"]
        df_mensal_exib["Quantidade"] = df_mensal_geral["Quantidade"].apply(
            formatar_inteiro
        )
        df_mensal_exib["Dif. Qtd"] = (
            df_mensal_geral["Var_Qtd"]
            .fillna(0)
            .apply(lambda x: f"{int(x):+,}".replace(",", ".") if x != 0 else "-")
        )
        df_mensal_exib["% Qtd"] = (
            df_mensal_geral["Var_Qtd_Pct"]
            .fillna(0)
            .apply(lambda x: f"{x:+.1f}%".replace(".", ",") if x != 0 else "-")
        )
        df_mensal_exib["Valor Total (R$)"] = df_mensal_geral[
            "Valor_Total"
        ].apply(formatar_moeda)
        df_mensal_exib["Dif. Valor"] = (
            df_mensal_geral["Var_Valor"]
            .fillna(0)
            .apply(
                lambda x: f"R$ {x:+,.2f}".replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
                if x != 0
                else "-"
            )
        )
        df_mensal_exib["% Valor"] = (
            df_mensal_geral["Var_Valor_Pct"]
            .fillna(0)
            .apply(lambda x: f"{x:+.1f}%".replace(".", ",") if x != 0 else "-")
        )

        st.dataframe(
            df_mensal_exib, use_container_width=True, hide_index=True, height=400
        )

        st.markdown("---")

        st.subheader("📦 Quantitativo e Valor de Materiais por Ano")
        anos_disponiveis = sorted(df["Ano"].unique(), reverse=True)

        for ano_item in anos_disponiveis:
            with st.expander(f"📁 Materiais Saídos no Ano de {ano_item}"):
                df_ano_mat = (
                    df[df["Ano"] == ano_item]
                    .groupby(["Código", "Especificação", "Unidade"], as_index=False)
                    .agg(
                        Quantidade=("Quantidade_num", "sum"),
                        Valor_Total=("Total_num", "sum"),
                        Pedidos=("N° da Saída", "nunique"),
                    )
                    .sort_values("Quantidade", ascending=False)
                )

                df_ano_mat_exib = df_ano_mat.copy()
                df_ano_mat_exib["Quantidade"] = df_ano_mat_exib[
                    "Quantidade"
                ].apply(formatar_inteiro)
                df_ano_mat_exib["Valor_Total"] = df_ano_mat_exib[
                    "Valor_Total"
                ].apply(formatar_moeda)
                df_ano_mat_exib.columns = [
                    "Código",
                    "Especificação",
                    "Unidade",
                    "Qtd. Saída",
                    "Valor Total (R$)",
                    "Nº de Pedidos",
                ]

                st.dataframe(
                    df_ano_mat_exib,
                    use_container_width=True,
                    hide_index=True,
                    height=350,
                )


# ==========================================
# ABA 3: CALENDÁRIO DE SAÍDAS
# ==========================================
with aba_calendario:
    st.title("📅 Calendário de Saídas de Materiais")
    st.markdown(
        "Visualize o total de pedidos saídos por dia no mês selecionado e consulte os materiais correspondentes."
    )

    if df.empty:
        st.warning("Nenhum registro encontrado para os filtros selecionados.")
    else:
        anos_disp = sorted(df["Ano"].unique(), reverse=True)
        col_cal1, col_cal2 = st.columns(2)

        with col_cal1:
            ano_selecionado = st.selectbox("Selecione o Ano:", anos_disp, index=0)

        df_ano_atual = df[df["Ano"] == str(ano_selecionado)]
        meses_disp = sorted(df_ano_atual["Mes_Num"].unique())

        mes_atual_padrao = (
            datetime.datetime.now().month
            if datetime.datetime.now().month in meses_disp
            else (meses_disp[0] if meses_disp else 1)
        )

        with col_cal2:
            mes_selecionado = st.selectbox(
                "Selecione o Mês:",
                options=meses_disp,
                format_func=lambda x: f"{x:02d} - {MESES_ABREV_PT.get(x, '')}",
                index=(
                    meses_disp.index(mes_atual_padrao)
                    if mes_atual_padrao in meses_disp
                    else 0
                ),
            )

        st.markdown("---")

        df_mes = df_ano_atual[df_ano_atual["Mes_Num"] == mes_selecionado].copy()

        df_dias = (
            df_mes.groupby(df_mes["Data_dt"].dt.date, as_index=False)
            .agg(
                Total_Pedidos=("N° da Saída", "nunique"),
                Quantidade_Total=("Quantidade_num", "sum"),
                Valor_Total=("Total_num", "sum"),
            )
            .sort_values("Data_dt")
        )

        st.subheader(
            f"Resumo Diário de Saídas - {MESES_ABREV_PT.get(mes_selecionado, '')}/{ano_selecionado}"
        )

        if df_dias.empty:
            st.info("Não há registros de saídas para o mês e filtros selecionados.")
        else:
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric(
                "📦 Total de Pedidos no Mês",
                formatar_inteiro(df_mes["N° da Saída"].nunique()),
            )
            mc2.metric(
                "📊 Qtd. Itens Saídos",
                formatar_inteiro(df_mes["Quantidade_num"].sum()),
            )
            mc3.metric(
                "💰 Valor Total do Mês", formatar_moeda(df_mes["Total_num"].sum())
            )

            st.markdown("---")

            st.markdown("### 🗓️ Visão de Calendário do Mês")
            cal = calendar.Calendar(firstweekday=6)
            matriz_mes = cal.monthdayscalendar(
                int(ano_selecionado), int(mes_selecionado)
            )

            dias_semana = [
                "Domingo",
                "Segunda",
                "Terça",
                "Quarta",
                "Quinta",
                "Sexta",
                "Sábado",
            ]
            cols_cabecalho = st.columns(7)
            for idx, d_sem in enumerate(dias_semana):
                cols_cabecalho[idx].markdown(f"**{d_sem}**")

            dict_pedidos_dia = (
                df_dias.set_index("Data_dt")["Total_Pedidos"].to_dict()
            )

            for semana in matriz_mes:
                cols_semana = st.columns(7)
                for i, dia in enumerate(semana):
                    with cols_semana[i]:
                        if dia == 0:
                            st.markdown(
                                "<div style='color: #ccc; text-align:center; padding:10px;'>-</div>",
                                unsafe_allow_html=True,
                            )
                        else:
                            data_atual_obj = datetime.date(
                                int(ano_selecionado),
                                int(mes_selecionado),
                                dia,
                            )
                            pedidos_no_dia = dict_pedidos_dia.get(
                                data_atual_obj, 0
                            )

                            if pedidos_no_dia > 0:
                                st.markdown(
                                    f"""
                                    <div style='background-color: #e6f2ff; border: 1px solid #094780; border-radius: 5px; padding: 8px; text-align: center;'>
                                        <b>{dia:02d}</b><br>
                                        <span style='color: #094780; font-size: 12px;'>🛒 {pedidos_no_dia} pedido(s)</span>
                                    </div>
                                    """,
                                    unsafe_allow_html=True,
                                )
                            else:
                                st.markdown(
                                    f"""
                                    <div style='background-color: #f9f9f9; border: 1px solid #e0e0e0; border-radius: 5px; padding: 8px; text-align: center; color: #888;'>
                                        <b>{dia:02d}</b><br>
                                        <span style='font-size: 12px;'>Sem saída</span>
                                    </div>
                                    """,
                                    unsafe_allow_html=True,
                                )

            st.markdown("---")

            st.subheader("📋 Detalhamento de Materiais por Data")
            datas_com_saida = [
                d.strftime("%d/%m/%Y") for d in df_dias["Data_dt"]
            ]

            if datas_com_saida:
                data_escolhida_str = st.selectbox(
                    "Selecione o dia para inspecionar os materiais que saíram:",
                    datas_com_saida,
                )
                data_obj = datetime.datetime.strptime(
                    data_escolhida_str, "%d/%m/%Y"
                ).date()

                df_dia_detalhe = df_mes[
                    df_mes["Data_dt"].dt.date == data_obj
                ].copy()

                if not df_dia_detalhe.empty:
                    df_tabela_dia = (
                        df_dia_detalhe.groupby(
                            [
                                "N° da Saída",
                                "Código",
                                "Especificação",
                                "Unidade",
                                col_setor,
                                "DESTINO",
                                "MUNICÍPIO",
                            ],
                            as_index=False,
                        )
                        .agg(
                            Quantidade=("Quantidade_num", "sum"),
                            Valor_Total=("Total_num", "sum"),
                        )
                        .sort_values("Quantidade", ascending=False)
                    )

                    df_tabela_dia_exib = df_tabela_dia.copy()
                    df_tabela_dia_exib["Quantidade"] = df_tabela_dia_exib[
                        "Quantidade"
                    ].apply(formatar_inteiro)
                    df_tabela_dia_exib["Valor_Total"] = df_tabela_dia_exib[
                        "Valor_Total"
                    ].apply(formatar_moeda)
                    df_tabela_dia_exib.columns = [
                        "Nº Saída",
                        "Código",
                        "Material",
                        "Unidade",
                        "Setor",
                        "Destino",
                        "Município",
                        "Qtd. Saída",
                        "Valor Total (R$)",
                    ]

                    st.dataframe(
                        df_tabela_dia_exib,
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.info("Nenhum material encontrado para esta data.")


# ==========================================
# ABA 4: PREVISÃO DE DURAÇÃO (MONITORAMENTO)
# ==========================================
with aba_previsao:
    st.title("⏳ Monitoramento de Previsão de Duração de Estoque")
    st.markdown(
        "Acompanhamento independente da autonomia de estoque em meses conforme a demanda, ordenado do menor para o maior tempo de duração."
    )

    if df_inv_raw is None or df_inv_raw.empty:
        st.error(
            "⚠️ Não foi possível carregar o inventário do Google Planilhas "
            "(verifique o compartilhamento da planilha) ou ele está vazio."
        )
    else:
        c_modo, c_pdf = st.columns([2.5, 1.5])

        with c_modo:
            modo_visualizacao = st.radio(
                "Exibição da tabela:",
                options=[
                    "Todos os materiais (ordem crescente)",
                    "Apenas materiais críticos (< 4 meses)",
                ],
                horizontal=True,
            )

        apenas_criticos = modo_visualizacao.startswith("Apenas")
        if apenas_criticos:
            df_prev_exib = df_inv_raw[
                df_inv_raw["Previsao_num"] < 4.0
            ].copy()
        else:
            df_prev_exib = df_inv_raw.copy()

        df_prev_exib = df_prev_exib.sort_values(
            "Previsao_num", ascending=True
        ).reset_index(drop=True)

        with c_pdf:
            st.write("")
            if not df_prev_exib.empty:
                pdf_buffer = gerar_pdf_previsao(df_prev_exib, apenas_criticos)
                nome_pdf = (
                    "previsao_estoque_criticos.pdf"
                    if apenas_criticos
                    else "previsao_estoque_completo.pdf"
                )
                st.download_button(
                    label="📄 Baixar Relatório em PDF",
                    data=pdf_buffer,
                    file_name=nome_pdf,
                    mime="application/pdf",
                    use_container_width=True,
                )

        st.markdown("---")

        total_monitorado = len(df_inv_raw)
        total_criticos = int((df_inv_raw["Previsao_num"] < 4.0).sum())
        menor_previsao = df_inv_raw["Previsao_num"].min()

        p1, p2, p3 = st.columns(3)
        p1.metric("📦 Materiais Monitorados", formatar_inteiro(total_monitorado))
        p2.metric("🚨 Críticos (< 4 meses)", formatar_inteiro(total_criticos))
        p3.metric(
            "⏱️ Menor Duração",
            f"{formatar_decimal_br(menor_previsao, 1)} meses",
        )

        st.markdown("---")

        if df_prev_exib.empty:
            st.info(
                "Nenhum material com previsão de duração menor que 4 meses encontrado."
            )
        else:
            tabela_tela = pd.DataFrame(
                {
                    "Material": df_prev_exib["Material"],
                    "Descrição": df_prev_exib["Descrição"],
                    "Emb. padrão": df_prev_exib["Emb. padrão"],
                    "Estoque": df_prev_exib["Estoque_exib_num"].apply(
                        lambda x: formatar_decimal_br(x, 1)
                    ),
                    "Previsão de duração (meses)": df_prev_exib[
                        "Previsao_num"
                    ].apply(lambda x: f"{formatar_decimal_br(x, 1)} meses"),
                }
            )

            st.dataframe(
                tabela_tela,
                use_container_width=True,
                hide_index=True,
                height=500,
            )