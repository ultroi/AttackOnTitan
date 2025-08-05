from datetime import datetime, timedelta
from typing import List
from database.models import BankAccount, Player 

# --- Constants ---
BANK_OPEN_LEVEL = 15
BANK_OPEN_FEE = {'marks': 5000, 'valor': 1500, 'crystal': 10}
## DEPOSIT_CAPS removed: No weekly deposit cap
PENALTY_BASE = 2.5
PENALTY_DAILY_INCREASE = 1.5
LATE_DEPOSIT_PENALTY = 5.0

# Tax System Constants
TAX_THRESHOLDS = {
    'marks': 80000,
    'valor': 1500,
    'crystal': 500
}
TAX_RATE = 0.08  # 8% tax rate

# Used for richness calculation to avoid magic numbers
CURRENCY_VALUES = {
    'marks': 1,
    'valor': 5000,
    'crystal': 100000
}


class BankSystem:
    def __init__(self, db):
        self.db = db

    async def open_bank(self, player: Player) -> str:
        """Opens a bank account for a player, deducting the fee."""
        if player.level < BANK_OPEN_LEVEL:
            return f"⚠️ You must reach Level {BANK_OPEN_LEVEL} to open an account."

        account = await self.db.get_bank_account(player.user_id)
        if account and account.opened:
            return "✅ You already have an open bank account."

        # Check if player has enough funds in their inventory
        if player.marks < BANK_OPEN_FEE['marks']:
            return f"❌ Insufficient Marks. You need `{BANK_OPEN_FEE['marks']}`."
        if player.valor < BANK_OPEN_FEE['valor']:
            return f"❌ Insufficient Valor. You need `{BANK_OPEN_FEE['valor']}`."
        if player.crystal < BANK_OPEN_FEE['crystal']:
            return f"❌ Insufficient Crystals. You need `{BANK_OPEN_FEE['crystal']}`."

        # Deduct opening fee from player's inventory
        player.marks -= BANK_OPEN_FEE['marks']
        player.valor -= BANK_OPEN_FEE['valor']
        player.crystal -= BANK_OPEN_FEE['crystal']
        await self.db.save_player(player)

        # Create and save the new bank account
        new_account = BankAccount(
            user_id=player.user_id,
            opened=True,
            opened_at=datetime.now(),
            penalty_rate=PENALTY_BASE
        )
        await self.db.save_bank_account(new_account)
        return "✅ **Success!** Your bank account has been opened."

    async def deposit(self, player: Player, account: BankAccount, currency: str, amount: int) -> str:
        """Deposits currency from player's inventory to their bank account (no weekly cap)."""
        player_balance = getattr(player, currency, 0)
        if amount > player_balance:
            return f"❌ Insufficient funds. You only have `{player_balance}` {currency} in your inventory."

        # Perform the transaction
        setattr(player, currency, player_balance - amount)
        bank_balance_field = f"{currency}_balance"
        setattr(account, bank_balance_field, getattr(account, bank_balance_field) + amount)

        account.last_deposit = datetime.now()

        # Save both objects to the database
        await self.db.save_player(player)
        await self.db.save_bank_account(account)

        return f"✅ Successfully deposited `{amount}` {currency}."

    async def withdrawal(self, player: Player, account: BankAccount, currency: str, amount: int) -> str:
        """Withdraws currency from bank account to player's inventory."""
        bank_balance_field = f"{currency}_balance"
        bank_balance = getattr(account, bank_balance_field, 0)
        new_balance = bank_balance - amount
        if amount > bank_balance:
            return f"❌ Insufficient bank balance. You only have `{bank_balance}` {currency} in your account."

        # Perform the transaction
        setattr(account, bank_balance_field, new_balance)
        player_balance = getattr(player, currency, 0)
        setattr(player, currency, player_balance + amount)

        # Save both objects to the database
        await self.db.save_player(player)
        await self.db.save_bank_account(account)

        return f"✅ Successfully withdrew `{amount}` {currency}."

    async def get_central_bank_stats(self):
        """Returns overall stats for the /cb command (no weekly cap)."""
        all_accounts = await self.db.get_all_bank_accounts()

        # Calculate total reserves for each currency
        total_reserve = {
            'marks': sum(getattr(acc, 'marks_balance', 0) for acc in all_accounts),
            'valor': sum(getattr(acc, 'valor_balance', 0) for acc in all_accounts),
            'crystal': sum(getattr(acc, 'crystal_balance', 0) for acc in all_accounts)
        }

        # Calculate total wealth for sorting
        for acc in all_accounts:
            acc.total_wealth = (
                acc.marks_balance * CURRENCY_VALUES['marks'] +
                acc.valor_balance * CURRENCY_VALUES['valor'] +
                acc.crystal_balance * CURRENCY_VALUES['crystal']
            )

        # Sort accounts by total wealth
        sorted_accounts = sorted(all_accounts, key=lambda acc: acc.total_wealth, reverse=True)

        top_3 = [{'user_id': acc.user_id, 'total': acc.total_wealth} for acc in sorted_accounts[:3]]

        return {
            'total_reserve': total_reserve,
            'top_3_richest': top_3
        }

    def get_player_bank_info(self, account: BankAccount):
        """Gets a player's bank balance (no weekly quota)."""
        return {
            'marks': account.marks_balance,
            'valor': account.valor_balance,
            'crystal': account.crystal_balance
        }

    def apply_opening_penalty(self, player: Player, account: BankAccount) -> str:
        # Penalty starts 1 week after crossing level 15
        if not account.opened and player.level >= BANK_OPEN_LEVEL:
            if account.penalty_start_date is None:
                account.penalty_start_date = datetime.now() + timedelta(days=7)
                self.db.save_bank_account(account)
                return "Penalty period will start in 7 days."
            if datetime.now() >= account.penalty_start_date:
                # Calculate penalty rate
                days_since_start = (datetime.now() - account.penalty_start_date).days
                penalty_rate = PENALTY_BASE + days_since_start * PENALTY_DAILY_INCREASE
                # Deduct penalty from player inventory
                player.marks = int(player.marks * (1 - penalty_rate / 100))
                player.valor = int(player.valor * (1 - penalty_rate / 100))
                player.crystal = int(player.crystal * (1 - penalty_rate / 100))
                self.db.save_player(player)
                account.penalty_applied = True
                account.penalty_rate = penalty_rate
                self.db.save_bank_account(account)
                return f"Penalty applied: {penalty_rate:.2f}% deducted from your inventory."
        return "No penalty applied."


    def get_current_tax_rate(self, account: BankAccount):
        # Returns current penalty/tax rate for player
        if account.penalty_start_date and not account.opened:
            days_since_start = (datetime.now() - account.penalty_start_date).days
            return PENALTY_BASE + days_since_start * PENALTY_DAILY_INCREASE
        return 0.0

    async def check_and_apply_midnight_tax(self) -> List[dict]:
        tax_reports = []
        all_players = await self.db.get_all_players()
        central_bank = await self.db.get_bank_account("central_bank")
        
        if not central_bank:
            # Create central bank account if it doesn't exist
            central_bank = BankAccount(
                user_id="central_bank",
                opened=True,
                opened_at=datetime.now(),
                marks_balance=0,
                valor_balance=0,
                crystal_balance=0
            )

        for player in all_players:
            tax_report = {"user_id": player.user_id, "taxes": {}, "messages": []}
            tax_applied = False

            # Check each currency
            for currency in ["marks", "valor", "crystal"]:
                player_balance = getattr(player, currency)
                if player_balance > TAX_THRESHOLDS[currency]:
                    # Calculate tax
                    tax_amount = int(player_balance * TAX_RATE)
                    
                    # Deduct tax from player
                    setattr(player, currency, player_balance - tax_amount)
                    
                    # Add tax to central bank
                    bank_balance_field = f"{currency}_balance"
                    current_bank_balance = getattr(central_bank, bank_balance_field)
                    setattr(central_bank, bank_balance_field, current_bank_balance + tax_amount)
                    
                    tax_report["taxes"][currency] = tax_amount
                    # Add user message for this currency
                    tax_report["messages"].append(
                        f"💸 Tax Alert: `{tax_amount}` {currency} has been deducted from your account as tax."
                    )
                    tax_applied = True

            if tax_applied:
                await self.db.save_player(player)
                tax_reports.append(tax_report)

        # Save central bank changes
        await self.db.save_bank_account(central_bank)
        
        return tax_reports