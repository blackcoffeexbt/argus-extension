async def m001_wallets(db):
    await db.execute("""
        CREATE TABLE arguswallet.wallets (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            name TEXT NOT NULL,
            network TEXT NOT NULL,
            descriptor TEXT,
            revision INTEGER NOT NULL DEFAULT 0,
            snapshot TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE arguswallet.drafts (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            expires BIGINT NOT NULL,
            unsigned TEXT NOT NULL,
            signed_hex TEXT
        )
    """)
