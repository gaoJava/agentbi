-- InsightPilot / AgentBI demonstration dataset
-- PostgreSQL 17, UTF-8

CREATE TABLE IF NOT EXISTS public.video_game_sales (
    year double precision,
    na_sales double precision,
    eu_sales double precision,
    global_sales double precision,
    jp_sales double precision,
    other_sales double precision,
    rank bigint,
    genre text,
    name text,
    platform text,
    publisher text
);

COMMENT ON TABLE public.video_game_sales IS 'Video game sales demo data; sales columns are in millions of units.';
COMMENT ON COLUMN public.video_game_sales.year IS 'Release year';
COMMENT ON COLUMN public.video_game_sales.na_sales IS 'North America sales (million units)';
COMMENT ON COLUMN public.video_game_sales.eu_sales IS 'Europe sales (million units)';
COMMENT ON COLUMN public.video_game_sales.jp_sales IS 'Japan sales (million units)';
COMMENT ON COLUMN public.video_game_sales.other_sales IS 'Other regions sales (million units)';
COMMENT ON COLUMN public.video_game_sales.global_sales IS 'Global sales (million units)';
COMMENT ON COLUMN public.video_game_sales.rank IS 'Original global sales rank';
COMMENT ON COLUMN public.video_game_sales.genre IS 'Game genre';
COMMENT ON COLUMN public.video_game_sales.name IS 'Game title';
COMMENT ON COLUMN public.video_game_sales.platform IS 'Game platform';
COMMENT ON COLUMN public.video_game_sales.publisher IS 'Publisher';

CREATE INDEX IF NOT EXISTS ix_video_game_sales_platform ON public.video_game_sales (platform);
CREATE INDEX IF NOT EXISTS ix_video_game_sales_genre ON public.video_game_sales (genre);
CREATE INDEX IF NOT EXISTS ix_video_game_sales_publisher ON public.video_game_sales (publisher);

