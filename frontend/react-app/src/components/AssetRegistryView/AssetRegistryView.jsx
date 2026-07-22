import React, { useContext } from 'react';
import { AppContext } from '../../context/AppContext';
import AssetList from './AssetList';
import AssetDetail from './AssetDetail';
import AssetChat from './AssetChat';

export default function AssetRegistryView() {
  const { selectedAsset, setSelectedAsset } = useContext(AppContext);

  return (
    <>
      <AssetList selectedAsset={selectedAsset} onSelect={setSelectedAsset} />
      <AssetDetail asset={selectedAsset} />
      <AssetChat asset={selectedAsset} />
    </>
  );
}
