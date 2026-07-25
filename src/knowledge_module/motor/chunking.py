"""
chunking.py — partición genérica de texto largo en fragmentos con overlap (Capa 1).

Función pura, sin dependencia de DB ni de dominio. La usa cualquier instancia que necesite
hacer buscable por fragmento un texto completo ya guardado (decisión ya tomada: ~500 tokens,
50 de overlap, respetando párrafos). Cada instancia declara su propio tipo_ficha "*_chunk" +
tipo_conexion "chunk_de" en su plantilla; esta función solo resuelve la partición, agnóstica de
qué campo se está troceando o a qué tipo de ficha padre pertenece.

Aproxima tokens con palabras (separadas por espacio) en vez de usar el tokenizer real del
modelo de embeddings — para dimensionar el tamaño de un chunk de búsqueda esa precisión no es
necesaria: importa que el fragmento tenga contexto suficiente y un tamaño manejable, no un
conteo exacto de tokens del modelo.
"""

import re


def chunk_texto(texto: str, tamano_tokens: int = 500, overlap_tokens: int = 50) -> list[dict]:
    """
    Parte `texto` en fragmentos ordenados de ~tamano_tokens palabras, con overlap_tokens
    palabras repetidas entre fragmentos consecutivos para no perder contexto en los cortes.

    Prioriza no partir párrafos: si un párrafo entero entra en el fragmento actual, se agrega
    completo. Si un párrafo por sí solo excede tamano_tokens (frecuente en papers con abstracts
    largos o secciones sin subdivisión), se corta en ventanas de palabras dentro de ese párrafo.

    Args:
        texto: texto completo a trocear (ya extraído y saneado).
        tamano_tokens: tamaño objetivo de cada fragmento, en palabras (aprox. tokens).
        overlap_tokens: palabras repetidas entre el final de un fragmento y el inicio del
                        siguiente.

    Returns:
        Lista de {"texto": str, "orden": int}, en orden de lectura. Lista vacía si `texto`
        está vacío o solo tiene espacios.
    """
    if not texto or not texto.strip():
        return []
    if overlap_tokens >= tamano_tokens:
        raise ValueError("overlap_tokens debe ser menor que tamano_tokens")

    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    if not parrafos:
        parrafos = [texto.strip()]

    fragmentos: list[str] = []
    actual: list[str] = []
    paso = tamano_tokens - overlap_tokens

    for parrafo in parrafos:
        palabras = parrafo.split()

        if len(palabras) > tamano_tokens:
            # el párrafo por sí solo excede el tamaño — vaciar lo acumulado (fragmento propio,
            # sin forzar el overlap dentro de la ventana para no exceder tamano_tokens) y
            # partir este párrafo en sus propias ventanas con overlap interno.
            if actual:
                fragmentos.append(" ".join(actual))
            i = 0
            while i < len(palabras):
                ventana = palabras[i : i + tamano_tokens]
                fragmentos.append(" ".join(ventana))
                i += paso
            # lo que sigue (próximo párrafo) arranca con el overlap de la última ventana
            actual = fragmentos[-1].split()[-overlap_tokens:] if overlap_tokens else []
            continue

        if len(actual) + len(palabras) > tamano_tokens:
            fragmentos.append(" ".join(actual))
            actual = actual[-overlap_tokens:] if overlap_tokens else []

        actual.extend(palabras)

    if actual:
        fragmentos.append(" ".join(actual))

    return [{"texto": frag, "orden": i} for i, frag in enumerate(fragmentos)]
