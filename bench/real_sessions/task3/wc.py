import argparse
import counter


def main():
    parser = argparse.ArgumentParser(description='Count words in a file.')
    parser.add_argument('file', help='Path to the file to analyze')
    args = parser.parse_args()

    with open(args.file) as f:
        text = f.read()

    total = counter.count_words(text)
    top = counter.top_words(text, 5)

    print(f'Total words: {total}')
    print('Top 5 words:')
    for word, count in top:
        print(f'  {word}: {count}')


if __name__ == '__main__':
    main()
