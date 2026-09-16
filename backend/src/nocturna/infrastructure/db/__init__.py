"""Persistencia SQLAlchemy de Nocturna.

Este paquete traduce entre las entidades de dominio (`domain/entities.py`) y
las tablas de PostgreSQL. Deliberadamente **no reexporta** los modelos ORM de
`models.py`: quien necesite un repositorio importa `repositories.py`, y quien
necesite la fábrica de sesión importa `session.py`. Que un modelo ORM sea
importable desde `nocturna.infrastructure.db` haría trivial que se colara,
por comodidad, en `application/` o en `api/`.
"""
