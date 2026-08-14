# Compute the pseudo-random sequence corresponding to the LFSR noise generator

LFSR_BITS = 20
LFSR_TAP0 = 0
LFSR_TAP1 = 17

def lfsr_steps(
    steps: int,
    seed: int = 0x00001,
    b: int = LFSR_BITS,
    tap0: int = LFSR_TAP0,
    tap1: int = LFSR_TAP1,
) -> int:
    """Compute LFSR after a certain number of updates."""
    if not(seed):
        raise ValueError("Seed cannot be zero.")
    lfsr = seed
    for _ in range(steps):
        bit = ((lfsr >> LFSR_TAP1) ^ (lfsr >> LFSR_TAP0)) & 1
        lfsr = (lfsr >> 1) | (bit << (LFSR_BITS - 1))
    return lfsr

if __name__ == "__main__":
    seed = 0x0abba
    lfsr = seed
    period = 0
    while True:
        lfsr = lfsr_steps(1, seed=lfsr)
        period += 1
        if lfsr == seed:
            print(f"Actual period: {period}, maximum possible: {2**LFSR_BITS - 1}")
            break
    
            

    

