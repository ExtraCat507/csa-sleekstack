def main() {
    let high = 0;
    let low = 999999999;
    let add_low = 1;
    let base = 1000000000;

    low = low + add_low;

    if (low >= base) {
        low = low - base;
        high = high + 1;
    }

    if (high == 1) {
        print("1 ");
    }

    if (low == 0) {
        print("0 ");
    }
}
