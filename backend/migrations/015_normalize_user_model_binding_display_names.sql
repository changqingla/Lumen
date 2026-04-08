UPDATE user_model_bindings
SET display_name = TRIM(SUBSTRING(display_name FROM POSITION(' / ' IN display_name) + 3))
WHERE POSITION(' / ' IN display_name) > 0;
