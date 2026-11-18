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


async def m002_address_notes(db):
    await db.execute(f"""
        CREATE TABLE arguswallet.notes (
            wallet TEXT NOT NULL REFERENCES {db.references_schema}wallets(id) ON DELETE CASCADE,
            address TEXT NOT NULL,
            note TEXT NOT NULL,
            PRIMARY KEY (wallet, address)
        )
    """)


async def m003_wallet_history_preferences(db):
    await db.execute(
        "ALTER TABLE arguswallet.wallets ADD COLUMN gap_limit INTEGER NOT NULL DEFAULT 20"
    )
    await db.execute(f"""
        CREATE TABLE arguswallet.labels (
            wallet TEXT NOT NULL REFERENCES {db.references_schema}wallets(id) ON DELETE CASCADE,
            reference TEXT NOT NULL,
            label TEXT NOT NULL,
            PRIMARY KEY (wallet, reference)
        )
    """)
