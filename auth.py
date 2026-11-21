"""
auth.py
Tela de login para o painel. Os usuários vêm de uma planilha do Google com as
colunas: nome | usuario | senha | cargo | setor   (opcional: ativo)

A coluna "senha" pode conter a senha em texto normal (visível na planilha)
ou, opcionalmente, um hash gerado com gerar_hash.py (começa com "pbkdf2_sha256$").
"""
import hashlib
import hmac
import math
import secrets
import threading
import time
import unicodedata

import pandas as pd
import streamlit as st

# ==========================================================
# CONFIGURAÇÕES
# ==========================================================


def _cfg(chave, padrao):
    try:
        return st.secrets[chave]
    except Exception:
        return padrao


ID_USUARIOS = _cfg("ID_PLANILHA_USUARIOS", "1MgMAwlA_ViiZzWTYIcLX4twxPxFVdoTORoCIy4KXuhk")
GID_USUARIOS = _cfg("GID_USUARIOS", "0")

ITERACOES = 600_000            # custo do hash PBKDF2-SHA256
TIMEOUT_INATIVIDADE = 60 * 60  # desloga após 60 min sem interação
MAX_TENTATIVAS = 5             # falhas seguidas antes de bloquear
BLOQUEIO_SEGUNDOS = 5 * 60     # duração do bloqueio
TTL_USUARIOS = 120             # a lista de usuários é relida a cada 2 min

COLUNAS_OBRIGATORIAS = ["nome", "usuario", "senha", "cargo", "setor"]


# ==========================================================
# HASH DE SENHA (PBKDF2-SHA256, apenas biblioteca padrão)
# ==========================================================


def gerar_hash(senha, iteracoes=ITERACOES):
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", senha.encode("utf-8"), salt.encode("utf-8"), iteracoes
    )
    return f"pbkdf2_sha256${iteracoes}${salt}${dk.hex()}"


def verificar_hash(senha, armazenado):
    try:
        alg, it, salt, hash_hex = str(armazenado).strip().split("$")
        it = int(it)
        if alg != "pbkdf2_sha256" or not (100_000 <= it <= 5_000_000):
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", senha.encode("utf-8"), salt.encode("utf-8"), it
        )
        return hmac.compare_digest(dk.hex(), hash_hex.strip().lower())
    except Exception:
        return False


def verificar_senha(senha, armazenado):
    """Aceita senha em texto puro (comparação segura) ou hash PBKDF2."""
    armazenado = str(armazenado).strip()
    if armazenado.startswith("pbkdf2_sha256$"):
        return verificar_hash(senha, armazenado)
    if not armazenado:
        return False
    return hmac.compare_digest(
        senha.encode("utf-8"), armazenado.encode("utf-8")
    )


# hash falso para gastar o mesmo tempo quando o usuário não existe
_HASH_FALSO = gerar_hash(secrets.token_hex(8))


# ==========================================================
# USUÁRIOS (Google Planilhas)
# ==========================================================


def _sem_acento(txt):
    nfkd = unicodedata.normalize("NFKD", str(txt).replace("\ufeff", ""))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip().lower()


@st.cache_data(ttl=TTL_USUARIOS, show_spinner=False)
def _carregar_usuarios():
    if not ID_USUARIOS:
        raise ValueError("ID da planilha de usuários não configurado.")

    url = (
        f"https://docs.google.com/spreadsheets/d/"
        f"{ID_USUARIOS}/export?format=csv&gid={GID_USUARIOS}"
    )
    df = pd.read_csv(url, dtype=str).fillna("")
    df.columns = [_sem_acento(c) for c in df.columns]

    faltando = [c for c in COLUNAS_OBRIGATORIAS if c not in df.columns]
    if faltando:
        raise ValueError(f"Colunas ausentes na planilha de usuários: {faltando}")

    df["chave"] = df["usuario"].str.strip().str.casefold()
    df = df[df["chave"] != ""]

    if "ativo" in df.columns:
        inativos = {"nao", "n", "0", "false", "inativo", "bloqueado"}
        df = df[~df["ativo"].apply(_sem_acento).isin(inativos)]

    df = df.drop_duplicates("chave", keep="first")

    return {
        r["chave"]: {
            "nome": r["nome"].strip(),
            "senha": r["senha"].strip(),
            "cargo": r["cargo"].strip(),
            "setor": r["setor"].strip(),
        }
        for _, r in df.iterrows()
    }


# ==========================================================
# BLOQUEIO POR TENTATIVAS (compartilhado entre todas as sessões)
# ==========================================================


@st.cache_resource
def _estado_tentativas():
    return {"lock": threading.Lock(), "dados": {}}


def _segundos_bloqueio(chave):
    est = _estado_tentativas()
    with est["lock"]:
        reg = est["dados"].get(chave)
        if reg and reg["bloqueado_ate"] > time.time():
            return reg["bloqueado_ate"] - time.time()
    return 0


def _registrar_falha(chave):
    est = _estado_tentativas()
    agora = time.time()
    with est["lock"]:
        if len(est["dados"]) > 5000:
            est["dados"].clear()
        reg = est["dados"].setdefault(chave, {"falhas": 0, "bloqueado_ate": 0})
        if reg["bloqueado_ate"] and reg["bloqueado_ate"] <= agora:
            reg["falhas"], reg["bloqueado_ate"] = 0, 0
        reg["falhas"] += 1
        if reg["falhas"] >= MAX_TENTATIVAS:
            reg["bloqueado_ate"] = agora + BLOQUEIO_SEGUNDOS
            reg["falhas"] = 0


def _limpar_falhas(chave):
    est = _estado_tentativas()
    with est["lock"]:
        est["dados"].pop(chave, None)


# ==========================================================
# SESSÃO E TELA DE LOGIN
# ==========================================================


def usuario_atual():
    return st.session_state.get("usuario")


def sair(mensagem=None):
    st.session_state.clear()
    if mensagem:
        st.session_state["auth_msg"] = mensagem
    st.rerun()


def _tela_login():
    _, centro, _ = st.columns([1, 1.2, 1])
    with centro:
        st.markdown("## 🔐 Acesso ao Painel")
        st.caption("Informe suas credenciais para continuar.")

        msg = st.session_state.pop("auth_msg", None)
        if msg:
            st.info(msg)

        with st.form("form_login", clear_on_submit=False):
            usuario = st.text_input("Usuário", max_chars=100)
            senha = st.text_input("Senha", type="password", max_chars=200)
            enviar = st.form_submit_button("Entrar", use_container_width=True)

        if not enviar:
            return

        if not usuario.strip() or not senha:
            st.warning("Preencha usuário e senha.")
            return

        chave = usuario.strip().casefold()[:100]

        restante = _segundos_bloqueio(chave)
        if restante > 0:
            st.error(
                f"Muitas tentativas inválidas. Tente novamente em "
                f"{math.ceil(restante / 60)} min."
            )
            return

        try:
            usuarios = _carregar_usuarios()
        except Exception:
            st.error("Não foi possível validar o acesso agora. Tente novamente em instantes.")
            return

        reg = usuarios.get(chave)
        senha_ok = verificar_senha(senha, reg["senha"] if reg else _HASH_FALSO)

        if reg and senha_ok:
            _limpar_falhas(chave)
            st.session_state["auth_ok"] = True
            st.session_state["auth_ultimo"] = time.time()
            st.session_state["usuario"] = {
                "login": chave,
                "nome": reg["nome"],
                "cargo": reg["cargo"],
                "setor": reg["setor"],
            }
            st.rerun()
        else:
            _registrar_falha(chave)
            time.sleep(1)  # desacelera ataques de força bruta
            st.error("Usuário ou senha inválidos.")


def exigir_login():
    """Chame logo após st.set_page_config(). Interrompe o app se não houver login."""
    if st.session_state.get("auth_ok"):
        inativo = time.time() - st.session_state.get("auth_ultimo", 0)
        if inativo > TIMEOUT_INATIVIDADE:
            sair("Sessão expirada por inatividade. Entre novamente.")
        st.session_state["auth_ultimo"] = time.time()
        return

    _tela_login()
    st.stop()


def painel_usuario_sidebar():
    """Mostra quem está logado e o botão Sair na barra lateral."""
    u = usuario_atual()
    if not u:
        return
    st.sidebar.markdown(f"👤 **{u['nome']}**")
    st.sidebar.caption(f"{u['cargo']} · {u['setor']}")
    if st.sidebar.button("🚪 Sair", use_container_width=True):
        sair("Você saiu do painel.")
    st.sidebar.markdown("---")