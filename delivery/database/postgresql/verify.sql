\pset pager off
SELECT current_setting('server_version') AS postgresql_version,
       current_database() AS database_name,
       pg_encoding_to_char(encoding) AS encoding
FROM pg_database
WHERE datname = current_database();

SELECT count(*) AS video_game_sales_rows,
       count(DISTINCT platform) AS platforms,
       count(DISTINCT genre) AS genres,
       count(DISTINCT publisher) AS publishers
FROM public.video_game_sales;

SELECT platform, round(sum(global_sales)::numeric, 2) AS global_sales
FROM public.video_game_sales
GROUP BY platform
ORDER BY global_sales DESC
LIMIT 3;

