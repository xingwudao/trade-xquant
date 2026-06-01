from __future__ import annotations

from trade_xquant.models import AccountSnapshot, OrderPlan, PlannedOrder, Position, RebalanceTask


class PortfolioError(ValueError):
    pass


class PortfolioEngine:
    lot_size = 100

    def build_plan(
        self,
        task: RebalanceTask,
        account: AccountSnapshot,
        holdings: list[Position],
        prices: dict[str, float],
    ) -> OrderPlan:
        target_sum = sum(target.target_weight for target in task.targets)
        if target_sum > 1 + 1e-9:
            raise PortfolioError("target weights cannot exceed 1")
        if account.total_asset <= 0:
            raise PortfolioError("total_asset must be positive")

        target_weights = {target.symbol: target.target_weight for target in task.targets}
        positions = {position.symbol: position for position in holdings}
        symbols = set(target_weights) | set(positions)
        for symbol in symbols:
            if symbol not in prices or prices[symbol] <= 0:
                raise PortfolioError(f"missing or invalid price for {symbol}")

        min_amount = task.constraints.min_order_amount
        if min_amount is None:
            min_amount = 0
        max_single = task.constraints.max_single_order_amount
        max_turnover = task.constraints.max_turnover_ratio
        turnover_budget = None
        if max_turnover is not None:
            if max_turnover < 0.2:
                raise PortfolioError(
                    f"turnover ratio required by task exceeds max_turnover_ratio {max_turnover:.4f}"
                )
            turnover_budget = account.total_asset * max_turnover

        orders: list[PlannedOrder] = []
        sell_cash = 0.0

        for symbol in sorted(symbols):
            price = prices[symbol]
            current_qty = positions.get(symbol, Position(symbol=symbol, quantity=0, sellable_quantity=0)).quantity
            current_value = current_qty * price
            desired_value = account.total_asset * target_weights.get(symbol, 0.0)
            diff_value = desired_value - current_value
            if diff_value >= 0:
                continue
            quantity = min(positions[symbol].sellable_quantity, self._floor_lot(abs(diff_value) / price))
            if quantity <= 0:
                continue
            amount = quantity * price
            if turnover_budget is not None and amount > turnover_budget:
                quantity = self._floor_lot(turnover_budget / price)
                amount = quantity * price
            if quantity <= 0:
                continue
            orders.append(
                PlannedOrder(
                    task_id=task.task_id,
                    symbol=symbol,
                    side="sell",
                    quantity=quantity,
                    price=price,
                    amount=amount,
                    remark=task.task_id,
                )
            )
            sell_cash += amount
            if turnover_budget is not None:
                turnover_budget -= amount

        available_cash = account.cash + sell_cash
        available_cash -= account.total_asset * task.cash_buffer_ratio
        available_cash = max(0.0, available_cash)
        if turnover_budget is not None:
            available_cash = min(available_cash, max(0.0, turnover_budget))

        buy_candidates: list[tuple[str, float, float]] = []
        for symbol in sorted(target_weights):
            price = prices[symbol]
            current_qty = positions.get(symbol, Position(symbol=symbol, quantity=0, sellable_quantity=0)).quantity
            current_value = current_qty * price
            desired_value = account.total_asset * target_weights[symbol]
            diff_value = desired_value - current_value
            if diff_value > 0:
                buy_candidates.append((symbol, diff_value, price))

        for symbol, diff_value, price in buy_candidates:
            capped_value = min(diff_value, available_cash)
            if max_single is not None:
                capped_value = min(capped_value, max_single)
            if turnover_budget is not None:
                capped_value = min(capped_value, max(0.0, turnover_budget))
            quantity = self._floor_lot(capped_value / price)
            if quantity <= 0:
                continue
            amount = quantity * price
            if amount < min_amount:
                continue
            orders.append(
                PlannedOrder(
                    task_id=task.task_id,
                    symbol=symbol,
                    side="buy",
                    quantity=quantity,
                    price=price,
                    amount=amount,
                    remark=task.task_id,
                )
            )
            available_cash -= amount
            if turnover_budget is not None:
                turnover_budget -= amount

        turnover_amount = sum(order.amount for order in orders)
        turnover_ratio = turnover_amount / account.total_asset
        if max_turnover is not None and turnover_ratio > max_turnover + 1e-9:
            raise PortfolioError(
                f"turnover ratio {turnover_ratio:.4f} exceeds max_turnover_ratio {max_turnover:.4f}"
            )

        return OrderPlan(
            task_id=task.task_id,
            account_id=account.account_id,
            total_asset=account.total_asset,
            turnover_amount=turnover_amount,
            turnover_ratio=turnover_ratio,
            orders=orders,
        )

    def _floor_lot(self, shares: float) -> int:
        return int(shares // self.lot_size) * self.lot_size
