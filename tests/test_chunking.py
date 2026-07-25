import pytest

from knowledge_module.motor.chunking import chunk_texto


def test_texto_vacio_da_lista_vacia():
    assert chunk_texto("") == []
    assert chunk_texto("   \n\n  ") == []


def test_texto_corto_da_un_solo_fragmento():
    texto = "Este es un texto corto de prueba."
    fragmentos = chunk_texto(texto, tamano_tokens=50, overlap_tokens=5)
    assert len(fragmentos) == 1
    assert fragmentos[0]["orden"] == 0
    assert fragmentos[0]["texto"] == texto


def test_overlap_mayor_o_igual_a_tamano_lanza_error():
    with pytest.raises(ValueError):
        chunk_texto("cualquier texto", tamano_tokens=10, overlap_tokens=10)


def test_ordenes_son_secuenciales_desde_cero():
    palabras = " ".join(f"palabra{i}" for i in range(120))
    fragmentos = chunk_texto(palabras, tamano_tokens=20, overlap_tokens=5)
    assert len(fragmentos) > 1
    assert [f["orden"] for f in fragmentos] == list(range(len(fragmentos)))


def test_overlap_real_entre_fragmentos_consecutivos():
    palabras = " ".join(f"palabra{i}" for i in range(120))
    fragmentos = chunk_texto(palabras, tamano_tokens=20, overlap_tokens=5)
    for anterior, siguiente in zip(fragmentos, fragmentos[1:]):
        cola = anterior["texto"].split()[-5:]
        inicio = siguiente["texto"].split()[:5]
        assert cola == inicio


def test_no_pierde_contenido_ningun_palabra_falta():
    palabras_originales = [f"palabra{i}" for i in range(200)]
    texto = " ".join(palabras_originales)
    fragmentos = chunk_texto(texto, tamano_tokens=30, overlap_tokens=5)
    vistas = set()
    for f in fragmentos:
        vistas.update(f["texto"].split())
    assert vistas == set(palabras_originales)


def test_respeta_parrafos_cuando_entran_completos():
    p1 = " ".join(f"a{i}" for i in range(10))
    p2 = " ".join(f"b{i}" for i in range(10))
    texto = f"{p1}\n\n{p2}"
    fragmentos = chunk_texto(texto, tamano_tokens=25, overlap_tokens=3)
    # ambos párrafos entran juntos en un solo fragmento (10+10=20 <= 25)
    assert len(fragmentos) == 1
    assert p1.split()[0] in fragmentos[0]["texto"]
    assert p2.split()[-1] in fragmentos[0]["texto"]


def test_parrafo_individual_mas_grande_que_tamano_se_parte():
    parrafo_gigante = " ".join(f"w{i}" for i in range(100))
    fragmentos = chunk_texto(parrafo_gigante, tamano_tokens=20, overlap_tokens=5)
    assert len(fragmentos) > 1
    for f in fragmentos:
        assert len(f["texto"].split()) <= 20
