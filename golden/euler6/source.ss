// Euler problem 6:
// difference between the square of the sum and the sum of squares
// for numbers from 1 to 100.

def main() {
    let i = 1;
    let sum = 0;
    let sum_sq = 0;
    let result = 0;

    while (i <= 100) {
        sum = sum + i;
        sum_sq = sum_sq + i * i;
        i = i + 1;
    }

    result = sum * sum - sum_sq;
    print(result);
}
