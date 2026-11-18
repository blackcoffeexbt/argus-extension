# Argus Wallet for LNbits

A standalone Python extension for LNbits 1.6.2. Each LNbits user can create named Mainnet or Testnet4 wallets and pair an Argus BIP84 account with each one. The extension uses its own database and API at `/arguswallet/api/v1/wallets`. Existing core wallets and browser pairings are separate. To view an existing account here, pair the same signer on the same network and sync.

## Install

From this directory, run `python3 package_extension.py --base-url https://YOUR-HOST/arguswallet`, substituting the HTTPS directory where your LNbits server can fetch the release. Serve the two files from `dist/` there. In LNbits admin settings, add the hosted `manifest.json` URL as a Python extension source. Install **Argus Wallet** from Extensions, activate it, then enable it for your user. Opening the extension takes you to `/arguswallet/`. All browser assets ship in the archive. LNbits supplies the Python dependencies.

## Use

1. Create a wallet, selecting the network configured on the Argus. Open pairing on the device. Scan or paste its code in the wallet and approve the browser on the device.
2. Sync to discover funds. The extension queries mempool.space for the selected network, walking both receive and change branches until it finds 20 consecutive unused addresses beyond previously observed activity. A failed scan preserves the previous snapshot. The receive address updates after sync observes activity.
3. Select coins, enter recipients and a fee rate in sat/vB, then request a signature. The device controls approval. If it requests a remote PIN, the browser sends it over the request-bound encrypted session.
4. Review each signed output, change and fee. **Broadcast transaction** submits that signed transaction. After an uncertain broadcast result, check the displayed transaction ID before retrying. Payment drafts expire after 30 minutes.

The extension stores the public descriptor, address activity, coins and payment drafts on the server. Pairing credentials stay in the browser. Public address queries and confirmed broadcasts go to mempool.space. Clearing browser storage requires a new device pairing.

## Development checks

Run the Python tests with the LNbits development environment and the parent of this directory on `PYTHONPATH`: `python -m pytest tests/test_wallet.py`. Tests use temporary SQLite databases, synthetic coins, a simulated signer and a simulated explorer. `npm ci && npm test` checks the bundled signer and runs the protocol tests. `npm run build` rebuilds the browser signer from source.
