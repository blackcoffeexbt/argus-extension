# Argus Wallet for LNbits

A standalone Python extension for LNbits 1.6.2. Each LNbits user can create named Mainnet or Testnet4 wallets and pair an Argus BIP84 account with each one. The extension uses its own database and API at `/arguswallet/api/v1/wallets`. Existing core wallets and browser pairings are separate. To view an existing account here, pair the same signer on the same network and sync.

## Install

1. In LNbits admin settings, open **Extensions → Extension Sources**. Add the raw manifest URL and save your changes.

   `https://raw.githubusercontent.com/blackcoffeexbt/argus-extension/main/manifest.json`

2. Open **Extensions**, install **Argus Wallet**, then activate it and enable it for your user. Open the wallet at `/arguswallet/`.

LNbits discovers installable versions from published GitHub releases in this repository.

## Use

1. Create a wallet, selecting the network configured on the Argus. Open pairing on the device. Scan or paste its code in the wallet and approve the browser on the device.
2. In LNbits block explorer settings, enable the explorer and select the Electrum server URL and network for this wallet. Sync to discover funds. The extension queries the configured server, walking both receive and change branches until it reaches the configured number of consecutive unused addresses beyond previously observed activity. In Wallet details, set the address gap limit (1–200, default 20) and select Save and sync. A failed scan preserves the previous snapshot. The receive address updates after sync observes activity.
3. Open **Send**. Choose manual, random, all, smaller-first or larger-first coin selection. Enter recipients or use **Max**. Pick a fee estimate from Electrum or enter a custom rate in sat/vB. Review the selected amount, estimated fee and change, then request a signature. You can also export an unsigned PSBT and import the signed result. The device controls approval. If it requests a remote PIN, the browser sends it over the request-bound encrypted session.
4. Review each signed output, change and fee. **Broadcast transaction** submits that signed transaction. After an uncertain broadcast result, check the displayed transaction ID before retrying. Payment drafts expire after 30 minutes.

The extension stores the public descriptor, address activity, coins and payment drafts on the server. Pairing credentials stay in the browser. Scans, fee estimates, previous-transaction lookups and user-confirmed broadcasts use the configured Electrum server. Transactions, Addresses and UTXOs show the last complete scan. Click a column heading to sort or a row to expand. Each table supports search and editable labels. Confirmation counts and block timestamps are reported by the configured Electrum server at the last sync; block inclusion and headers are not independently verified. Use a trusted Electrum server. Transaction balances follow ledger order across sorting and filtering. Clearing browser storage requires a new device pairing.

## Development checks

Run the Python tests with the LNbits development environment and the parent of this directory on `PYTHONPATH`: `python -m pytest tests/test_wallet.py`. Tests use temporary SQLite databases, synthetic coins, a simulated signer and a simulated explorer. `npm ci && npm test` checks the bundled signer and runs the protocol tests. `npm run build` rebuilds the browser signer from source.
