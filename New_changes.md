GUI
1. the GUI needs some way to sort the items already present in the db, preferably by pressing the label and then the items get sorted by that column with option for either ascending or descending.

2. A nuclear erase button to completely clear the db, the button should be double validated, requiring a button press and hold or something else which cannot be done accidently.

3. The manual Entry section is almost entire off screen, need a way to scroll the page or make it its own panel, prefered option is scrolling.

4. Need a settings button incase manual reassignment of db, username, password is required.

Required Fixes:
1. bug with item logic is not updating quantities in the db, just adding new items with the updated quantity
eg:"I have removed 250 grams of active dry yeast" should reduce the existing quantity by 250 grams instead it just adds a new item of qty 250 and unit grams
