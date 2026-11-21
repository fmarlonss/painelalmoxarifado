"""
dados_google.py
Baixa as planilhas direto do Google Sheets e aplica os mesmos tratamentos
dos scripts originais (dados_tratados.csv e inventario_com_previsao.csv).
"""
import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# ==========================================================
# CONFIGURAÇÕES
# (opcional: sobrescreva via .streamlit/secrets.toml ou
#  "Secrets" do Streamlit Cloud, para não expor IDs no GitHub)
# ==========================================================


def _cfg(chave, padrao):
    try:
        return st.secrets[chave]
    except Exception:
        return padrao


ID_SAIDAS = _cfg("ID_PLANILHA_SAIDAS", "1m1uB2Mp7uzIA6IwtFnKoNKTS5ZW5CHAgIFqtuboiS00")
GID_2025 = "0"
GID_2026 = "2124367263"
GID_UNIDADES = "109418330"

ID_INVENTARIO = _cfg(
    "ID_PLANILHA_INVENTARIO", "1OBECDaYVDj3nlPuvAOp5KV3XNd3j9zuO_C7NLFQ-yqo"
)
GID_INVENTARIO = "0"

TTL_SEGUNDOS = 3600  # recarrega sozinho a cada 1h (o botão força antes)


def _ler_aba(id_planilha, gid):
    url = (
        f"https://docs.google.com/spreadsheets/d/"
        f"{id_planilha}/export?format=csv&gid={gid}"
    )
    return pd.read_csv(url)


# ==========================================================
# SAÍDAS (equivalente ao dados_tratados.csv)
# ==========================================================


def _tratar_saidas(df, ano):
    df = df.iloc[27:].reset_index(drop=True)

    df["Transação"] = df.iloc[:, 0].where(df.iloc[:, 2].notna(), pd.NA)
    df["Transação"] = df["Transação"].ffill()

    colunas_preenchimento = df.columns[[5, 8, 2]]
    df[colunas_preenchimento] = df[colunas_preenchimento].ffill()

    df = df.iloc[2:].reset_index(drop=True)
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)
    df = df.iloc[:-7].copy()

    df["Código"] = (
        df["Código"]
        .astype("string")
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.lstrip("0")
    )

    df = df.dropna(subset=["Código", "Especificação"])

    df = df[
        ~df["Código"]
        .astype("string")
        .str.strip()
        .str.lower()
        .isin(["transação", "valor total geral:", "total:", "código"])
    ]

    df = df.loc[:, df.columns.notna()]
    df = df.drop(columns=["Nº fabricante"], errors="ignore")

    if len(df.columns) <= 11:
        raise ValueError(
            f"A planilha de {ano} possui menos de 12 colunas. "
            "Verifique a estrutura dos dados."
        )

    df = df.rename(
        columns={
            df.columns[1]: "N° da Saída",
            df.columns[2]: "Data da Saída",
            df.columns[4]: "Unidade",
            df.columns[11]: "Transação",
        }
    )
    return df


def _montar_saidas():
    data_2025 = _tratar_saidas(_ler_aba(ID_SAIDAS, GID_2025), 2025)
    data_2026 = _tratar_saidas(_ler_aba(ID_SAIDAS, GID_2026), 2026)
    data_complete = pd.concat([data_2025, data_2026], ignore_index=True)

    data_unidades = _ler_aba(ID_SAIDAS, GID_UNIDADES)[
        [
            "SETOR",
            "MUNICÍPIO",
            "DESTINO",
            "GRAU",
            "ENTRÂNCIA",
            "REFERÊNCIA DO MUNICÍPIO",
        ]
    ]

    data_complete = data_complete.merge(
        data_unidades, left_on="Unidade", right_on="SETOR", how="left"
    )
    data_complete = data_complete.drop(
        columns=["Localização", "Desdobr.", "Transação", "SETOR"],
        errors="ignore",
    )
    return data_complete


# ==========================================================
# INVENTÁRIO (equivalente ao inventario_tratados.csv)
# ==========================================================


def _tratar_inventario(dados):
    dados = dados.iloc[10:].reset_index(drop=True)
    dados.columns = dados.iloc[0]
    dados = dados[1:].reset_index(drop=True)
    dados.columns.name = None

    dados = dados.dropna(subset=["Descrição"]).reset_index(drop=True)
    dados = dados[dados["Descrição"] != "Descrição"]
    dados = dados[dados["Descrição"].str.strip() != "Descrição"]
    dados = dados.reset_index(drop=True)

    dados = dados.dropna(axis=1, how="all")
    dados["Material"] = (
        dados["Material"].astype(str).str.replace(".", "", regex=False)
    )
    return dados


# ==========================================================
# FUNÇÕES PÚBLICAS (com cache)
# ==========================================================


@st.cache_data(ttl=TTL_SEGUNDOS, show_spinner="Baixando saídas do Google Planilhas...")
def obter_saidas():
    """Retorna o equivalente ao dados_tratados.csv (tudo como texto)."""
    return _montar_saidas().fillna("").astype(str)


@st.cache_data(ttl=TTL_SEGUNDOS, show_spinner="Baixando inventário do Google Planilhas...")
def obter_inventario_previsao():
    """Retorna o equivalente ao inventario_com_previsao.csv (tudo como texto)."""
    inventario = _tratar_inventario(_ler_aba(ID_INVENTARIO, GID_INVENTARIO))
    saidas = obter_saidas().copy()
    inventario = inventario.fillna("").astype(str)

    inventario.columns = inventario.columns.str.strip()
    saidas.columns = saidas.columns.str.strip()

    inventario["Material"] = inventario["Material"].str.strip()
    saidas["Código"] = saidas["Código"].str.strip()

    saidas["Data da Saída"] = pd.to_datetime(
        saidas["Data da Saída"].str.strip(), format="%d/%m/%Y", errors="coerce"
    )
    saidas["Quantidade"] = pd.to_numeric(
        saidas["Quantidade"]
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False),
        errors="coerce",
    ).fillna(0)

    inventario["Estoque_atual"] = pd.to_numeric(
        inventario["Estoque(D)"]
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False),
        errors="coerce",
    ).fillna(0)

    data_referencia = saidas["Data da Saída"].max()
    if pd.isna(data_referencia):
        raise ValueError("Não foram encontradas datas válidas na planilha de saídas.")
    data_inicial = data_referencia - pd.DateOffset(months=6)

    saidas_6m = saidas[
        (saidas["Data da Saída"] > data_inicial)
        & (saidas["Data da Saída"] <= data_referencia)
    ]

    resumo = (
        saidas_6m.groupby("Código", as_index=False)["Quantidade"]
        .sum()
        .rename(
            columns={
                "Código": "Material",
                "Quantidade": "Total de saídas (6 meses)",
            }
        )
    )

    resultado = inventario.merge(resumo, on="Material", how="left")
    resultado["Total de saídas (6 meses)"] = resultado[
        "Total de saídas (6 meses)"
    ].fillna(0)

    resultado["Média mensal (6 meses)"] = (
        resultado["Total de saídas (6 meses)"] / 6
    ).round(1)

    resultado["Previsão de duração (meses)"] = (
        resultado["Estoque_atual"]
        / resultado["Média mensal (6 meses)"].replace(0, float("nan"))
    ).round(1)

    resultado["Material"] = resultado["Material"].astype(str).str.strip()
    excecoes = ["260001", "260002", "260003", "260156"]
    resultado = resultado[
        ((~resultado["Material"].str.startswith("26")) | resultado["Material"].isin(excecoes))
        & (~resultado["Material"].str.startswith("91"))
        & (resultado["Média mensal (6 meses)"] > 0)
    ].copy()

    resultado = resultado.drop(columns=["Estoque(R)", "Estoque(C)"], errors="ignore")
    return resultado.fillna("").astype(str)


@st.cache_data(ttl=TTL_SEGUNDOS)
def momento_atualizacao():
    """Hora em que os dados foram baixados (zera quando o cache é limpo)."""
    return datetime.datetime.now(ZoneInfo("America/Fortaleza"))