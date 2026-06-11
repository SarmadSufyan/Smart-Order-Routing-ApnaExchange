# ALGORITHMS.md — Core Algorithms with Pseudocode & Complexity Analysis

---

## 1. Best Price Routing Algorithm

### Significance

The Best Price strategy is the default and most fundamental routing algorithm in any SOR system. It ensures regulatory compliance with best execution obligations (MiFID II, Reg NMS) by always attempting to fill orders at the best available price across all accessible venues. This algorithm is the backbone of the routing engine.

### Pseudocode

```
FUNCTION route_best_price(order, market_snapshot, venue_health, risk_limits):
    // Step 1: Filter eligible venues
    eligible_venues ← []
    FOR EACH venue IN market_snapshot:
        IF venue.status == BLACKLISTED: SKIP
        IF venue.status == DISCONNECTED: SKIP
        IF venue.data_age > STALENESS_THRESHOLD: SKIP
        IF venue.id IN risk_limits.restricted_venues: SKIP
        IF pre_trade_check(order, venue) == REJECTED: SKIP
        eligible_venues.APPEND(venue)

    IF eligible_venues IS EMPTY:
        RETURN RoutingResult(status=REJECTED, reason="No eligible venues")

    // Step 2: Sort by price
    IF order.side == BUY:
        SORT eligible_venues BY ask_price ASC  // cheapest first
    ELSE:
        SORT eligible_venues BY bid_price DESC  // highest first

    // Step 3: Greedy allocation
    child_orders ← []
    remaining_qty ← order.quantity

    FOR EACH venue IN eligible_venues:
        IF remaining_qty <= 0: BREAK

        IF order.side == BUY:
            available ← venue.ask_size
            price ← venue.ask_price
        ELSE:
            available ← venue.bid_size
            price ← venue.bid_price

        alloc_qty ← MIN(remaining_qty, available)
        child_orders.APPEND(ChildOrder(venue.id, alloc_qty, price))
        remaining_qty ← remaining_qty - alloc_qty

    // Step 4: Handle unfilled remainder
    IF remaining_qty > 0:
        IF order.order_type == MARKET:
            // Queue remainder for next cycle
            RETURN RoutingResult(status=PARTIAL, child_orders, remaining=remaining_qty)
        ELSE:
            RETURN RoutingResult(status=REJECTED, reason="Insufficient liquidity")

    RETURN RoutingResult(status=SUCCESS, child_orders)
```

### Complexity Analysis

| Metric | Complexity | Explanation |
|---|---|---|
| Time (best) | O(V) | V = number of venues; single pass if first venue fills entirely |
| Time (average) | O(V log V) | Dominated by sorting venues by price |
| Time (worst) | O(V log V) | Same as average (sorting is the bottleneck) |
| Space | O(V) | Store filtered venues + child orders |

### Comparison with Alternatives

| Algorithm | Time (Avg) | Space | Strengths | Weaknesses |
|---|---|---|---|---|
| **Best Price (ours)** | O(V log V) | O(V) | Simple, deterministic, best execution compliance | Doesn't consider market impact |
| Random Allocation | O(V) | O(V) | Fastest | No price optimization, fails compliance |
| Weighted Round Robin | O(V) | O(V) | Distributes load evenly | Ignores price differences |
| Optimal LP Solver | O(V³) | O(V²) | Globally optimal allocation | Too slow for real-time |

---

## 2. Venue Health Scoring Algorithm

### Significance

The health scoring algorithm converts multiple raw metrics (latency, fill rate, reject rate, uptime, data freshness) into a single composite score (0.0 to 1.0) that the routing engine uses to rank venues and the policy engine uses to trigger automated actions.

### Pseudocode

```
FUNCTION compute_health_score(venue_metrics, config):
    // Step 1: Normalize each metric to [0, 1] range
    latency_score ← 1.0 - CLAMP(venue_metrics.latency_p95 / config.max_latency, 0, 1)
    fill_score ← CLAMP(venue_metrics.fill_rate, 0, 1)
    reject_score ← 1.0 - CLAMP(venue_metrics.reject_rate, 0, 1)
    uptime_score ← CLAMP(venue_metrics.uptime, 0, 1)
    freshness_score ← 1.0 - CLAMP(venue_metrics.data_age_ms / config.max_staleness_ms, 0, 1)

    // Step 2: Weighted average
    weights ← {
        latency:    0.25,
        fill_rate:  0.25,
        reject:     0.20,
        uptime:     0.15,
        freshness:  0.15
    }

    score ← (
        weights.latency   * latency_score +
        weights.fill_rate * fill_score +
        weights.reject    * reject_score +
        weights.uptime    * uptime_score +
        weights.freshness * freshness_score
    )

    // Step 3: Determine status
    IF score >= 0.8: status ← HEALTHY
    ELSE IF score >= 0.5: status ← DEGRADED
    ELSE: status ← CRITICAL

    RETURN HealthResult(score, status)
```

### Complexity

| Metric | Complexity |
|---|---|
| Time | O(1) per venue, O(V) for all venues |
| Space | O(1) per venue |

---

## 3. Geometric Brownian Motion (GBM) Price Simulation

### Significance

GBM is the standard model for simulating stock price movements in quantitative finance. It produces realistic price paths with controllable volatility and drift. Our implementation adds regime-switching to simulate different market conditions (calm, volatile, trending).

### Pseudocode

```
FUNCTION simulate_price_step(current_price, dt, params):
    // Standard GBM: dS = μSdt + σSdW
    // where μ = drift, σ = volatility, dW = Wiener process increment

    mu ← params.drift           // e.g., 0.0001 (slight upward bias)
    sigma ← params.volatility   // e.g., 0.02 (2% daily vol)
    regime ← params.current_regime

    // Regime-switching: adjust volatility
    IF regime == CALM:
        sigma_effective ← sigma * 0.5
    ELSE IF regime == NORMAL:
        sigma_effective ← sigma
    ELSE IF regime == VOLATILE:
        sigma_effective ← sigma * 2.0
    ELSE IF regime == CRISIS:
        sigma_effective ← sigma * 4.0

    // Generate random normal increment
    dW ← RANDOM_NORMAL(0, SQRT(dt))

    // GBM formula (discretized)
    price_change ← current_price * (mu * dt + sigma_effective * dW)
    new_price ← current_price + price_change

    // Ensure price stays positive
    new_price ← MAX(new_price, 0.01)

    // Regime transition (Markov chain)
    IF RANDOM() < params.regime_transition_prob:
        new_regime ← RANDOM_CHOICE([CALM, NORMAL, VOLATILE, CRISIS],
                                     weights=params.transition_matrix[regime])
    ELSE:
        new_regime ← regime

    RETURN (new_price, new_regime)
```

### Complexity

| Metric | Complexity |
|---|---|
| Time per step | O(1) |
| Space | O(1) per venue |

### Comparison

| Model | Realism | Complexity | Fat Tails | Mean Reversion |
|---|---|---|---|---|
| **GBM + Regime** (ours) | Good | O(1) | Via regime | Via regime |
| Simple Random Walk | Poor | O(1) | No | No |
| GARCH | Better | O(p+q) | Yes | No |
| Heston (Stochastic Vol) | Best | O(1) | Yes | Yes |

---

## 4. RAFT Consensus Algorithm

### Significance

RAFT ensures all platform nodes agree on critical risk state. Without consensus, one node might blacklist a venue while another continues routing to it, creating inconsistent behavior. RAFT provides strong consistency: once a decision is committed, all nodes will eventually agree.

### Pseudocode (Leader Election)

```
// Each node starts as FOLLOWER

STATE: { role: FOLLOWER, current_term: 0, voted_for: null, log: [] }

FUNCTION on_election_timeout():
    // No heartbeat received from leader — start election
    STATE.role ← CANDIDATE
    STATE.current_term ← STATE.current_term + 1
    STATE.voted_for ← self.id
    votes_received ← 1  // vote for self

    FOR EACH node IN cluster_nodes (excluding self):
        SEND RequestVote(term=STATE.current_term, candidate_id=self.id,
                         last_log_index=LEN(STATE.log)-1,
                         last_log_term=STATE.log[-1].term IF log NOT EMPTY ELSE 0)

    // Wait for responses
    WHILE votes_received < MAJORITY AND NOT timeout:
        response ← RECEIVE_VOTE_RESPONSE()
        IF response.vote_granted:
            votes_received ← votes_received + 1

    IF votes_received >= MAJORITY:
        STATE.role ← LEADER
        start_heartbeat_loop()
    ELSE:
        STATE.role ← FOLLOWER
        reset_election_timer()

FUNCTION on_receive_vote_request(request):
    IF request.term < STATE.current_term:
        RETURN VoteResponse(vote_granted=False, term=STATE.current_term)

    IF request.term > STATE.current_term:
        STATE.current_term ← request.term
        STATE.role ← FOLLOWER
        STATE.voted_for ← null

    IF (STATE.voted_for IS null OR STATE.voted_for == request.candidate_id) AND
       candidate_log_is_up_to_date(request):
        STATE.voted_for ← request.candidate_id
        reset_election_timer()
        RETURN VoteResponse(vote_granted=True, term=STATE.current_term)

    RETURN VoteResponse(vote_granted=False, term=STATE.current_term)
```

### Pseudocode (Log Replication)

```
FUNCTION leader_replicate(command):
    // Append to local log
    entry ← LogEntry(term=STATE.current_term, index=LEN(STATE.log), command=command)
    STATE.log.APPEND(entry)

    // Replicate to followers
    ack_count ← 1  // self
    FOR EACH follower IN cluster_nodes:
        SEND AppendEntries(
            term=STATE.current_term,
            leader_id=self.id,
            prev_log_index=follower.next_index - 1,
            prev_log_term=STATE.log[follower.next_index - 1].term,
            entries=[entry],
            leader_commit=STATE.commit_index
        )

    // Wait for majority
    WHILE ack_count < MAJORITY:
        response ← RECEIVE_APPEND_RESPONSE()
        IF response.success:
            ack_count ← ack_count + 1
            follower.next_index ← entry.index + 1
        ELSE:
            // Follower log inconsistent — decrement and retry
            follower.next_index ← follower.next_index - 1
            RETRY_APPEND(follower)

    // Committed — apply to state machine
    STATE.commit_index ← entry.index
    apply_to_state_machine(command)
    RETURN CommitResult(success=True, index=entry.index)
```

### Complexity

| Operation | Time | Space |
|---|---|---|
| Leader election | O(N) messages per election | O(N) |
| Log replication | O(N) messages per entry | O(L) where L = log length |
| Read (from leader) | O(1) | O(1) |
| Commit | O(N) for majority ack | O(1) |

Where N = number of nodes (3 in our case).

### Comparison with Alternatives

| Algorithm | Consistency | Fault Tolerance | Understandability | Our Choice |
|---|---|---|---|---|
| **RAFT** (ours) | Strong | N/2 - 1 failures | High (designed for clarity) | ✅ |
| Paxos | Strong | N/2 - 1 failures | Very low (notoriously hard) | ❌ |
| ZAB (Zookeeper) | Strong | N/2 - 1 failures | Medium | ❌ |
| Gossip (eventual) | Eventual | Very high | Medium | ❌ (too weak) |

---

## 5. Square-Root Market Impact Model

### Significance

When a large order hits a venue, it moves the price. The square-root impact model (based on Almgren-Chriss) is the industry standard for estimating this impact. It is used to demonstrate realistic venue behavior.

### Pseudocode

```
FUNCTION compute_market_impact(order_size, daily_volume, volatility, side):
    // Square-root impact: ΔP = σ * sign(side) * sqrt(Q / V)
    // σ = daily volatility, Q = order size, V = daily volume

    participation_rate ← order_size / daily_volume
    impact ← volatility * SQRT(participation_rate)

    IF side == BUY:
        price_impact ← +impact  // pushes price up
    ELSE:
        price_impact ← -impact  // pushes price down

    RETURN price_impact  // as a fraction of current price
```

### Complexity

| Metric | Complexity |
|---|---|
| Time | O(1) |
| Space | O(1) |

---

## 6. Anomaly Detection (Statistical)

### Significance

The venue health monitor needs to detect when a venue's behavior deviates significantly from its historical norm. We use a z-score based approach with exponential moving average (EMA) for online computation.

### Pseudocode

```
FUNCTION detect_anomaly(new_value, metric_state):
    // Update EMA of mean and variance
    alpha ← 0.1  // smoothing factor (configurable)

    old_mean ← metric_state.ema_mean
    old_var ← metric_state.ema_variance

    // Welford's online algorithm with EMA
    diff ← new_value - old_mean
    new_mean ← old_mean + alpha * diff
    new_var ← (1 - alpha) * (old_var + alpha * diff * diff)

    metric_state.ema_mean ← new_mean
    metric_state.ema_variance ← new_var

    // Compute z-score
    IF new_var > 0:
        std_dev ← SQRT(new_var)
        z_score ← ABS(new_value - new_mean) / std_dev
    ELSE:
        z_score ← 0

    // Classify
    IF z_score > 3.0: RETURN ANOMALY_CRITICAL
    IF z_score > 2.0: RETURN ANOMALY_WARNING
    RETURN NORMAL
```

### Complexity

| Metric | Complexity |
|---|---|
| Time per observation | O(1) |
| Space per metric per venue | O(1) (just mean + variance) |
| Total space | O(V × M) where V = venues, M = metrics |
