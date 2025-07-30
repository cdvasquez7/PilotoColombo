import streamlit as st
import sqlite3
from model.classifier import is_bottle
import os
from PIL import Image, ImageOps
import pandas as pd

DB_PATH = "data/recycle.db"
IMG_PATH = "images/temp.jpg"
os.makedirs("images", exist_ok=True)

# ----------------- INICIALIZACIÓN ------------------
if "step" not in st.session_state:
    st.session_state.step = "inicio"
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "capture_validated" not in st.session_state:
    st.session_state.capture_validated = False
if "camera_key" not in st.session_state:
    st.session_state.camera_key = f"cam_{os.urandom(4).hex()}"
if "img_data" not in st.session_state:
    st.session_state.img_data = None

# ----------------- FUNCIONES ------------------
def get_user(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT name, points FROM users WHERE id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result

# ----------------- VISTA 1: INICIO ------------------
if st.session_state.step == "inicio":
    st.title("♻️ Reciclaje Inteligente")
    st.markdown("Bienvenido. Esta aplicación te permite registrar botellas recicladas y ganar un bono 🎁 si llegas a 15 puntos.")

    user_id = st.text_input("🔐 Ingresa tu cédula para iniciar")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("➡️ Ingresar"):
            with st.spinner("Buscando usuario..."):
                user = get_user(user_id)
                if user:
                    st.session_state.user_id = user_id
                    st.session_state.step = "dashboard"
                else:
                    st.session_state.temp_user_id = user_id
                    st.session_state.step = "registro"
            st.rerun()

    with col2:
        if st.button("📝 Registrarse"):
            st.session_state.temp_user_id = user_id
            st.session_state.step = "registro"
            st.rerun()

# ----------------- VISTA 1.5: REGISTRO ------------------
elif st.session_state.step == "registro":
    st.title("📝 Registro de Usuario")
    st.info("No encontramos tu cédula, por favor regístrate.")

    name = st.text_input("👤 Nombre completo")
    if st.button("✅ Confirmar Registro"):
        with st.spinner("Registrando usuario..."):
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("INSERT INTO users (id, name) VALUES (?, ?)", (st.session_state.temp_user_id, name))
            conn.commit()
            conn.close()
            st.success("🎉 Usuario registrado con éxito")
            st.session_state.user_id = st.session_state.temp_user_id
            st.session_state.step = "dashboard"
        st.rerun()

    if st.button("🔙 Volver"):
        st.session_state.step = "inicio"
        st.rerun()

# ----------------- VISTA 2: DASHBOARD ------------------
elif st.session_state.step == "dashboard":
    user = get_user(st.session_state.user_id)
    if user:
        name, points = user
        st.title(f"👋 Bienvenido, {name}")
        st.write(f"📊 Puntos acumulados: **{points}**")

        if points >= 15:
            st.success("🎟️ ¡Ya puedes redimir tu ticket para el bono del quiz!")

        if st.button("📷 Registrar nueva botella"):
            st.session_state.step = "capture"
            st.session_state.capture_validated = False
            st.session_state.camera_key = f"cam_{os.urandom(4).hex()}"
            st.session_state.img_data = None
            st.rerun()

        if st.button("🔄 Cerrar sesión"):
            st.session_state.user_id = None
            st.session_state.step = "inicio"
            st.rerun()

# ----------------- VISTA 3: CAPTURA ------------------
elif st.session_state.step == "capture":
    st.title("📷 Captura de botella")

    # Mostrar la cámara solo si no se ha validado aún
    if not st.session_state.capture_validated:
        st.session_state.img_data = st.camera_input("Toma una foto de la botella", key=st.session_state.camera_key)

        col1, col2 = st.columns([1, 1])
        with col1:
            if st.session_state.img_data and st.button("✅ Validar botella"):
                st.session_state.capture_validated = True
                st.rerun()
        with col2:
            if st.button("🔙 Volver al perfil"):
                st.session_state.step = "dashboard"
                st.rerun()
    else:
        # Procesar imagen validada
        with open(IMG_PATH, "wb") as f:
            f.write(st.session_state.img_data.getvalue())

        with st.spinner("Analizando imagen..."):
            is_valid, prediction = is_bottle(IMG_PATH)
            img = Image.open(IMG_PATH)
            border_color = "green" if is_valid else "red"
            img_bordered = ImageOps.expand(img, border=10, fill=border_color)
            st.image(img_bordered, caption="📸 Imagen analizada", use_container_width=True)

            # Registrar en base de datos
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()

            if is_valid:
                cursor.execute("UPDATE users SET points = points + 1 WHERE id = ?", (st.session_state.user_id,))
                cursor.execute("INSERT INTO history (user_id, valid) VALUES (?, ?)", (st.session_state.user_id, True))
                conn.commit()
                points = cursor.execute("SELECT points FROM users WHERE id = ?", (st.session_state.user_id,)).fetchone()[0]
                st.success("✅ ¡Botella aceptada! Punto ganado 🎉")
                st.markdown(f"🏆 **Puntos actuales: `{points}`**")

                if points >= 15:
                    st.balloons()
                    st.success("🎟️ ¡Ganaste un ticket para el bono!")
            else:
                cursor.execute("INSERT INTO history (user_id, valid) VALUES (?, ?)", (st.session_state.user_id, False))
                conn.commit()
                st.error("🚫 Esta imagen no parece una botella plástica.")
                points = cursor.execute("SELECT points FROM users WHERE id = ?", (st.session_state.user_id,)).fetchone()[0]
                st.markdown(f"📉 **Puntos actuales: `{points}`**")

            conn.close()

            with st.expander("🔍 Mostrar detalles de predicción"):
                df_pred = pd.DataFrame(prediction, columns=["ID", "Etiqueta", "Probabilidad"])
                df_pred["Probabilidad"] = df_pred["Probabilidad"].apply(lambda x: f"{x*100:.2f}%")
                st.table(df_pred[["Etiqueta", "Probabilidad"]])

        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("📸 Nueva foto"):
                st.session_state.capture_validated = False
                st.session_state.camera_key = f"cam_{os.urandom(4).hex()}"
                st.session_state.img_data = None
                st.rerun()
        with col2:
            if st.button("🔙 Volver al perfil"):
                st.session_state.step = "dashboard"
                st.rerun()
