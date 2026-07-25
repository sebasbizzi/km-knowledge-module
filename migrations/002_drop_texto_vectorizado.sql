-- Saca la columna texto_vectorizado de ficha — genérico, aplica a cualquier tenant/instancia
-- que use el motor (001_motor_generico.sql la declaraba como "transparencia/auditoría": ver el
-- texto embebido sin parsear props). Verificado: nada la lee, ni en el motor ni en ninguna
-- instancia — es puro peso muerto. IF EXISTS la hace idempotente para reruns.

ALTER TABLE ficha DROP COLUMN IF EXISTS texto_vectorizado;
