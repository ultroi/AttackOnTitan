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
        central_bank = next((acc for acc in all_accounts if acc.user_id == "central_bank"), None)

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

        # Sort accounts by total wealth and filter out system accounts
        player_accounts = [acc for acc in all_accounts if acc.user_id != "central_bank"]
        sorted_accounts = sorted(player_accounts, key=lambda acc: acc.total_wealth, reverse=True)

        top_3 = [{'user_id': acc.user_id, 'total': acc.total_wealth} for acc in sorted_accounts[:3]]

        # Get tax collection history
        tax_history = []
        if central_bank and hasattr(central_bank, 'tax_history'):
            tax_history = central_bank.tax_history[-5:]  # Get last 5 tax collections
        
        # Get last tax check time
        last_tax_check = None
        if central_bank and hasattr(central_bank, 'last_tax_check'):
            last_tax_check = central_bank.last_tax_check

        return {
            'total_reserve': total_reserve,
            'top_3_richest': top_3,
            'tax_history': tax_history,
            'last_tax_check': last_tax_check
        }

    def get_player_bank_info(self, account: BankAccount):
        """Gets a player's bank balance (no weekly quota)."""
        return {
            'marks': account.marks_balance,
            'valor': account.valor_balance,
            'crystal': account.crystal_balance
        }
        
    async def check_player_tax_status(self, player) -> dict:
        """Check if a player would be taxed and return tax info."""
        tax_info = {
            'would_be_taxed': False, 
            'taxes': {},
            'thresholds': TAX_THRESHOLDS,
            'tax_rate': f"{TAX_RATE * 100:.1f}%",
            'level_requirement_met': player.level >= 15,
            'level_requirement': 15
        }
        
        # If player is below level 15, they are exempt from taxes
        if player.level < 15:
            tax_info['exempt_reason'] = f"You are exempt from taxes until reaching level {tax_info['level_requirement']}."
            tax_info['would_be_taxed'] = False
            
            for currency in ["marks", "valor", "crystal"]:
                player_balance = getattr(player, currency)
                tax_info[f'{currency}_balance'] = player_balance
            
            return tax_info
        
        for currency in ["marks", "valor", "crystal"]:
            player_balance = getattr(player, currency)
            tax_info[f'{currency}_balance'] = player_balance
            
            if player_balance > TAX_THRESHOLDS[currency]:
                tax_amount = int(player_balance * TAX_RATE)
                tax_info['would_be_taxed'] = True
                tax_info['taxes'][currency] = tax_amount
        
        return tax_info

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
        """Apply midnight tax to all eligible players."""
        import logging
        logger = logging.getLogger("bank_system")
        
        tax_reports = []
        all_players = await self.db.get_all_players()
        central_bank = await self.db.get_bank_account("central_bank")
        
        # Get current datetime for comparison
        now = datetime.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        logger.info(f"Starting midnight tax check at {now}")
        logger.info(f"Today's date for comparison: {today}")
        
        # Initialize central bank if it doesn't exist
        if not central_bank:
            logger.info("Creating central bank account")
            central_bank = BankAccount(
                user_id="central_bank",
                opened=True,
                opened_at=now,
                marks_balance=0,
                valor_balance=0,
                crystal_balance=0,
                last_tax_check=None  # Set to None initially
            )
            await self.db.save_bank_account(central_bank)

        # Check if tax has already been applied today
        if central_bank.last_tax_check:
            last_check_date = central_bank.last_tax_check.date() if hasattr(central_bank.last_tax_check, 'date') else central_bank.last_tax_check
            current_date = today.date()
            
            logger.info(f"Last tax check: {last_check_date}, Current date: {current_date}")
            
            if str(last_check_date) == str(current_date):
                logger.info("Tax already applied today, skipping")
                return []
        else:
            logger.info("No previous tax check found, proceeding with tax collection")
        
        # Track total tax collected
        total_tax_collected = {"marks": 0, "valor": 0, "crystal": 0}
        players_taxed = 0
        
        logger.info(f"Processing {len(all_players)} players for tax collection")

        # Process each player
        for player in all_players:
            # Skip players without user_id
            if not player.user_id:
                continue

            tax_report = {"user_id": player.user_id, "taxes": {}, "messages": []}
            tax_applied = False
            
            # Check if player meets minimum level requirement for taxation (level 15+)
            if player.level < 15:
                logger.debug(f"Player {player.user_id} (level {player.level}) is below level 15, skipping taxation")
                continue
                
            # Check each currency for tax eligibility
            for currency in ["marks", "valor", "crystal"]:
                player_balance = getattr(player, currency, 0)
                
                logger.debug(f"Player {player.user_id} - {currency}: {player_balance} (threshold: {TAX_THRESHOLDS[currency]})")
                
                if player_balance > TAX_THRESHOLDS[currency]:
                    # Calculate tax
                    tax_amount = int(player_balance * TAX_RATE)
                    
                    logger.info(f"Taxing player {player.user_id}: {tax_amount} {currency}")
                    
                    # Deduct tax from player's inventory
                    new_balance = player_balance - tax_amount
                    setattr(player, currency, new_balance)
                    
                    # Add tax to central bank
                    bank_balance_field = f"{currency}_balance"
                    current_bank_balance = getattr(central_bank, bank_balance_field, 0)
                    setattr(central_bank, bank_balance_field, current_bank_balance + tax_amount)
                    
                    # Record tax information
                    tax_report["taxes"][currency] = tax_amount
                    total_tax_collected[currency] += tax_amount
                    
                    # Add user message for this currency
                    tax_report["messages"].append(
                        f"💸 Tax Alert: `{tax_amount}` {currency} has been deducted from your inventory as tax."
                    )
                    tax_applied = True

            # Save player data and record tax if applied
            if tax_applied:
                players_taxed += 1
                
                # Initialize tax history if it doesn't exist
                if not hasattr(player, 'tax_history') or player.tax_history is None:
                    player.tax_history = []
                
                # Add tax record to player's history
                tax_record = {
                    "date": today.isoformat(),
                    "taxes": tax_report["taxes"]
                }
                
                # Keep only last 10 records
                if isinstance(player.tax_history, list):
                    player.tax_history = player.tax_history[-9:] + [tax_record]
                else:
                    player.tax_history = [tax_record]
                
                # Save player with updated balances and history
                try:
                    await self.db.save_player(player)
                    logger.info(f"Successfully saved player {player.user_id} after tax collection")
                except Exception as e:
                    logger.error(f"Error saving player {player.user_id}: {e}")
                
                tax_reports.append(tax_report)

        # Update central bank with tax collection info
        central_bank.last_tax_check = today
        
        # Initialize central bank tax history if needed
        if not hasattr(central_bank, 'tax_history') or central_bank.tax_history is None:
            central_bank.tax_history = []
        
        # Add tax collection record
        tax_record = {
            "date": today.isoformat(),
            "total_collected": total_tax_collected,
            "players_taxed": players_taxed
        }
        
        # Keep only last 10 records
        if isinstance(central_bank.tax_history, list):
            central_bank.tax_history = central_bank.tax_history[-9:] + [tax_record]
        else:
            central_bank.tax_history = [tax_record]
        
        # Save central bank changes
        try:
            await self.db.save_bank_account(central_bank)
            logger.info(f"Central bank updated successfully. Total collected: {total_tax_collected}")
        except Exception as e:
            logger.error(f"Error saving central bank: {e}")
        
        logger.info(f"Tax collection completed. Taxed {players_taxed} players. Total: {total_tax_collected}")
        return tax_reports

    async def force_tax_execution(self) -> List[dict]:
        """Force tax execution for testing purposes."""
        import logging
        logger = logging.getLogger("bank_system")
        
        logger.info("Forcing tax execution (bypassing date check)")
        
        # Get central bank and reset last_tax_check
        central_bank = await self.db.get_bank_account("central_bank")
        if central_bank:
            # Set to yesterday to force execution
            yesterday = datetime.now() - timedelta(days=1)
            central_bank.last_tax_check = yesterday
            await self.db.save_bank_account(central_bank)
            logger.info(f"Reset central bank last_tax_check to {yesterday}")
        
        # Now run the tax collection
        return await self.check_and_apply_midnight_tax()