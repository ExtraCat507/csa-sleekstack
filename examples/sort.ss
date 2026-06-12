let count = 0;
let done = 0;
let ch = 0;
let a = 0;
let b = 0;
let c = 0;
let tmp = 0;

def trap() {
    ch = getc();

    if (ch == 10) {
        done = 1;
    } else {
        if (count == 0) {
            a = ch;
        } else {
            if (count == 1) {
                b = ch;
            } else {
                c = ch;
            }
        }

        count = count + 1;
    }

    iret();
}

def main() {
    while (done == 0) {
    }

    if (a > b) {
        tmp = a;
        a = b;
        b = tmp;
    }

    if (b > c) {
        tmp = b;
        b = c;
        c = tmp;
    }

    if (a > b) {
        tmp = a;
        a = b;
        b = tmp;
    }

    putc(a);
    putc(b);
    putc(c);
}
