from __future__ import annotations

from dotenv import load_dotenv

from config import private_key, wallet_address

load_dotenv()
USDC_DECIMALS = 1_000_000.0


def _format_usdc(raw: object) -> str:
    text = str(raw or "").strip()
    if not text:
        return "$0.00"
    try:
        amount = float(text)
    except (TypeError, ValueError):
        return text
    if "." not in text and "e" not in text.lower():
        amount /= USDC_DECIMALS
    return f"${amount:,.2f}"


def main() -> int:
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import AssetType, BalanceAllowanceParams

    client = ClobClient(
        "https://clob.polymarket.com",
        key=private_key(),
        chain_id=137,
        signature_type=0,
        funder=wallet_address(),
    )
    client.set_api_creds(client.create_or_derive_api_creds())

    signer = str(client.get_address() or "").strip().lower()
    configured = wallet_address()
    if configured and signer and configured != signer:
        print("Wallet mismatch: POLYGON_PRIVATE_KEY does not match POLYGON_WALLET_ADDRESS.")
        return 1

    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    print("Approving USDC collateral allowance for Polymarket...")
    result = client.update_balance_allowance(params)
    snapshot = client.get_balance_allowance(params)
    print(f"Allowance update response: {result}")
    if isinstance(snapshot, dict):
        balance = snapshot.get("balance", "0")
        allowances = snapshot.get("allowances") or {}
        formatted_allowances = {
            str(address): _format_usdc(amount)
            for address, amount in allowances.items()
        } if isinstance(allowances, dict) else allowances
        print(f"Current collateral balance: {_format_usdc(balance)}")
        print(f"Current collateral allowances: {formatted_allowances}")
    print("You only need to run this once per wallet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
