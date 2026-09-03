-- Run with psql from this directory after 001_schema.sql.
-- psql variable example: psql -v data_file="C:/path/video_game_sales.csv" ...

TRUNCATE TABLE public.video_game_sales;
\copy public.video_game_sales (year,na_sales,eu_sales,global_sales,jp_sales,other_sales,rank,genre,name,platform,publisher) FROM :'data_file' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');

DO $$
BEGIN
    IF (SELECT count(*) FROM public.video_game_sales) <> 16595 THEN
        RAISE EXCEPTION 'Seed validation failed: expected 16595 rows, got %',
            (SELECT count(*) FROM public.video_game_sales);
    END IF;
END $$;

ANALYZE public.video_game_sales;

