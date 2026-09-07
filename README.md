# tjvz (pronounced taj-veez)

Open-source recommendation system for focused reading.

### Principles:

1. Privacy: 
    -  No data leaves your device unless you want it to.

2. Control:
    - What makes it into your feed 
    - How the algorithm ranks items
    - How the algorithm learns from your feedback 

3. Interpretability: 
    - Explain why an item is highly ranked in your feed.

## Anatomy

1. Feed: You can subscribe to publications, authors, forums and other readers' archives (if they broadcast) via RSS/Atom. The items in the feed are ordered according to the score computed by your local algorithm.

2. Stack: You can promote items from the feed to the stack i.e. your reading list. Once you mark them as read, they leave the stack.

3. Archive: A private list of things you have read. Helps tjvz learn about your tastes and interests. You can sync data from your Zotero library or [Goodreads](https://help.goodreads.com/s/article/000001596). You can optionally broadcast your reading archive to share your reading with your friends and help them find signal amongst the noise.

### Broadcasting a reading list




NOTE: If your reading spans very disparate fields (like mine: maths/coding & languages/literature) it might be better to set up separate profiles for them.