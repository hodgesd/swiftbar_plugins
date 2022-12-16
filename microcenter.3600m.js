#!/usr/local/bin/node
/*
 * <xbar.title>jsSwiftBar</xbar.title>
 * <xbar.version>v1.0</xbar.version>
 * <xbar.author>hodgesd</xbar.author>
 * <xbar.author.github>hodgesd</xbar.author.github>
 * <xbar.desc>Microcenter Deal of the Day Sale</xbar.desc>
 * <xbar.dependencies>node</xbar.dependencies>
 * <xbar.abouturl></xbar.abouturl>
 */

const jsdom = require('jsdom');
const { JSDOM } = jsdom;

// Get the current date in the format DMonthName
// e.g. 13December
// todo: add leading zero to day if day < 10?
const date = new Date();
let day = date.getDate();
let monthName = date.toLocaleString('default', { month: 'long' });

// console.log(`${day}${monthName}`);

url = `https://www.microcenter.com/search/search_results.aspx?Ntt=5206&Ntx=mode+matchpartial&Ntk=adv&sortby=match&N=0&myStore=true`;

// Function to get the DOM of a webpage
async function getDOM(url) {
  return await JSDOM.fromURL(url);
}

console.log('MC' + '\n---\n');

getDOM(dealsURL).then((dom) => {
  const items = dom.window.document.querySelectorAll(
    '.instore.buyingrestriction'
  );
  const itemsArray = Array.from(items);
  const bannedCategories = [
    'bras',
    'garmin',
    'fujifilm',
    'military pride',
    'blue topaz',
    "burt's bees gift sets",
    'diamond jewelry',
  ];
  console.log(
    'Sale Items | href= https://www.microcenter.com/search/search_results.aspx?Ntt=5206&Ntx=mode+matchpartial&Ntk=adv&sortby=match&N=0&myStore=true' +
      '\n---\n'
  );
  // Loop through the items and output the item name, sale price, discount, and link
  itemsArray.forEach((item) => {
    const categoryName = item.parentElement
      .querySelector('a')
      .textContent.trim()
      .replace('select ', '')
      .split('Off ')[1];
    const categoryLink = item.querySelector('a').href;
    // Log the category name and link if it's not in the bannedCategories array
    const categoryMenuItem =
      categoryName && !bannedCategories.includes(categoryName.toLowerCase())
        ? `--${capitalizeFirstLetter(categoryName)}| href= ${categoryLink}`
        : '';
    console.log(categoryMenuItem);
  });
});
