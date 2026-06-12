def square(x) {
    return x * x;
}

def main() {
    let value = square(7);

    if (value == 49) {
        print("OK");
    } else {
        print("FAIL");
    }
}
